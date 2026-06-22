"""Arena Orchestrator:端到端编排 skill 竞技场。

本模块把 core-infra / fusion-engine / auto-tasks 的所有零件串成一条主流程:

    加载 skills → 加载/生成 tasks
        → 阶段 A(对比竞技):每个 task × 每个 skill 跑产物,两两配对 → v4-pro 评判 → Elo 更新
        → 阶段 B(融合):取 Elo Top2 skill → v4-pro 融合 → 落盘 v3
        → 阶段 C(自改进):取 Elo Bottom1 skill → 自改进循环 → ImprovementReport
        → 阶段 D(总报告):合并 Elo、融合结果、改进结果 → Markdown 报告

支持断点续跑:每完成一个阶段(以及阶段 A 的每对子任务)就把 state 写入
`reports/orchestrator_state.json`;下次启动时根据 state 跳过已完成步骤。

设计要点:
- 所有 API 调用(执行 / 评判 / 融合 / 改进)都通过 DeepSeekClient 单入口,便于 mock。
- 阶段 A 的产物会被缓存到 `reports/cache/runs/{task_id}__<skill_name|baseline>.txt`,
  既支持断点续跑,也便于事后回放。
- 阶段 B / C 的产物固定输出到 `reports/fused/` 与 `reports/improved/` 下,
  保持工作目录干净。
- 状态机:每个阶段有 pending / running / done / failed 四种状态,
  done 阶段在恢复时直接跳过(读 state.json 验证必填字段)。
- 完整 run_full_cycle 在 mock 客户端下应能在数秒内跑完(测试 5 用例 <= 30 秒)。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from .config import (
    ELO_STATE_FILE,
    REPORTS_DIR,
    SKILLS_DIR,
    TASKS_AUTO_DIR,
    TASKS_DIR,
    ensure_reports_dir,
)
from .deepseek_client import CompletionResult, DeepSeekClient
from .elo import load_domain_state, load_state, save_domain_state, save_state, update_rating
from .fuse import fuse_skills
from .judge import DimensionScores, Verdict, compare
from .report import MatchResult, generate_report
from .runner import RunOutput, load_skill, run_with_skill
from .self_improve import (
    Evaluator,
    ImprovementReport,
    run_improvement_cycle,
)
from .skill_metadata import SkillEntry, TASK_DOMAINS, load_skill_entry
from .task_dedup import TaskDeduplicator, jaccard_similarity
from .task_generator import Task, TaskGenerator

logger = logging.getLogger(__name__)


# -------- 状态文件 / 缓存路径 --------

ORCHESTRATOR_STATE_FILE: Path = REPORTS_DIR / "orchestrator_state.json"
RUNS_CACHE_DIR: Path = REPORTS_DIR / "cache" / "runs"
FUSED_DIR: Path = REPORTS_DIR / "fused"
IMPROVED_DIR: Path = REPORTS_DIR / "improved"
MATCHES_LOG: Path = REPORTS_DIR / "matches.jsonl"

# 状态 schema 版本号(改变 schema 时 +1,旧 state.json 视为不兼容)
STATE_SCHEMA_VERSION: int = 1


# -------- 数据结构 --------


@dataclass
class FullReport:
    """Orchestrator 端到端主流程的最终报告。

    Attributes:
        title: 报告标题。
        elo_state: 阶段 A 后的 Elo 快照(skill_name -> 分数)。
        matches: 阶段 A 全部比赛记录。
        fused_skill: 阶段 B 融合产物的保存路径(若执行)。
        fused_content: 阶段 B 融合产物文本。
        improvement: 阶段 C 自改进报告(若执行)。
        bottom_skill: 阶段 C 被改进的 skill 名称(若执行)。
        report_path: 最终 Markdown 报告文件路径。
        notes: 自由备注(适合打印"骨架 / partial / full"等状态)。
        raw_state: 完整的 orchestrator 状态(便于诊断)。
    """

    title: str
    elo_state: dict[str, float]
    matches: list[MatchResult] = field(default_factory=list)
    fused_skill: Path | None = None
    fused_content: str = ""
    improvement: ImprovementReport | None = None
    bottom_skill: str | None = None
    report_path: Path | None = None
    notes: str = ""
    raw_state: dict[str, Any] = field(default_factory=dict)


# -------- Orchestrator 主体 --------


class ArenaOrchestrator:
    """Skill 竞技场编排器(完整版)。

    用法:
        orch = ArenaOrchestrator()
        report = orch.run_full_cycle(
            skill_paths=["skills/concise-writer.md", "skills/detailed-writer.md"],
            task_source="fixed",
        )
        fused_path = orch.run_fusion("skills/A.md", "skills/B.md")
        impr = orch.run_self_improvement("skills/foo.md")
    """

    def __init__(
        self,
        *,
        client: DeepSeekClient | None = None,
        elo_state_path: Path | None = None,
        state_path: Path | None = None,
        runs_cache_dir: Path | None = None,
        fused_dir: Path | None = None,
        improved_dir: Path | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._client = client
        self._elo_state_path = elo_state_path
        self._state_path = state_path or ORCHESTRATOR_STATE_FILE
        self._runs_cache_dir = runs_cache_dir or RUNS_CACHE_DIR
        self._fused_dir = fused_dir or FUSED_DIR
        self._improved_dir = improved_dir or IMPROVED_DIR
        self._progress_cb = progress_callback

    def _emit(self, event: dict[str, Any]) -> None:
        """进度事件埋点:若注入了 progress_callback 则调用。

        事件协议见 arena.orchestrator 文档。所有事件 dict 必须包含 `type` 字段。
        异常被静默吞掉,避免回调错误中断编排。
        """
        if self._progress_cb is None:
            return
        try:
            self._progress_cb(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning("_emit 回调失败: %s", exc)

    # ============================================================
    # 端到端主入口
    # ============================================================

    def run_full_cycle(
        self,
        skill_paths: list[str],
        task_source: str = "fixed",
        auto_categories: list[str] | None = None,
        auto_per_category: int = 3,
        rounds_per_pair: int = 2,
        *,
        fused_output_name: str | None = None,
        max_improve_iterations: int = 2,
        run_fusion: bool = True,
        run_improvement: bool = True,
        report_title: str = "Skill 竞技场 · 全量循环报告",
        skip_state: bool = False,
    ) -> FullReport:
        """跑一次完整竞技场循环(阶段 A → B → C → D)。

        Args:
            skill_paths: 参与的 skill 文件路径列表(可含 baseline "baseline" 表示裸 prompt)。
            task_source: "fixed" 用 tasks/fixed/ 下的固定任务;"auto" 调 v4-pro 动态生成;
                "hybrid" 加载 fixed + auto 并去重。
            auto_categories: 当 task_source=auto/hybrid 时,生成任务的赛道列表;
                None 时使用默认(全部 6 条赛道,见 skill_metadata.TASK_DOMAINS)。
            auto_per_category: 每个类目生成多少个 auto 任务。
            rounds_per_pair: 每个 (task, skill_a, skill_b) 三元组跑多少轮 Elo(>=1)。
            fused_output_name: 阶段 B 融合产物的文件名(默认 `<a>__<b>_fused.md`)。
            max_improve_iterations: 阶段 C 自改进的最大迭代次数。
            run_fusion: 是否执行阶段 B(便于跳过或调试)。
            run_improvement: 是否执行阶段 C。
            report_title: 最终报告的标题。
            skip_state: True 时不读取也不写入 state.json(用于纯内存运行 / 测试)。

        Returns:
            FullReport,含 Elo 快照、阶段 A 比赛、融合 / 改进产物路径、最终报告路径。
        """
        if task_source not in ("fixed", "auto", "hybrid"):
            raise ValueError(
                f"task_source 必须是 fixed/auto/hybrid,实际为 {task_source!r}"
            )
        if rounds_per_pair < 1:
            raise ValueError("rounds_per_pair 必须 >= 1")
        if max_improve_iterations < 1:
            raise ValueError("max_improve_iterations 必须 >= 1")

        # ---- 1. 加载 / 准备 state ----
        if skip_state:
            state = self._new_state(skill_paths=skill_paths, task_source=task_source)
        else:
            ensure_reports_dir()
            state = self._load_state(skill_paths, task_source)
            # 若 skill_paths 与 state 中记录的不同,强制重置(简化设计)
            if state.get("skill_paths") != list(skill_paths):
                logger.info(
                    "skill_paths 与 state 不一致,重置 state: old=%s, new=%s",
                    state.get("skill_paths"),
                    skill_paths,
                )
                state = self._new_state(
                    skill_paths=skill_paths, task_source=task_source
                )

        # ---- 2. 加载 skill ----
        skills: dict[str, SkillEntry] = self._load_skills(skill_paths)

        # ---- 3. 加载 / 生成任务集 ----
        tasks: list[dict[str, Any]] = self._resolve_tasks(
            task_source=task_source,
            auto_categories=auto_categories,
            auto_per_category=auto_per_category,
        )

        self._emit({
            "type": "cycle_start",
            "task_source": task_source,
            "skill_count": len(skills),
            "task_count": len(tasks),
            "rounds_per_pair": rounds_per_pair,
            "max_improve_iterations": max_improve_iterations,
            "run_fusion": run_fusion,
            "run_improvement": run_improvement,
        })

        # ---- 阶段 A:对比竞技 + Elo ----
        self._emit({"type": "phase_start", "phase": "A"})
        matches: list[MatchResult] = self._phase_arena(
            state=state,
            skills=skills,
            tasks=tasks,
            rounds_per_pair=rounds_per_pair,
        )
        self._emit({"type": "phase_done", "phase": "A", "match_count": len(matches)})
        # Elo 状态从 domain_elo 读(阶段 A 已落盘)
        domain_elo = self._read_domain_elo_state()

        # ---- 阶段 B:按领域融合 Top2 ----
        fused_path: Path | None = None
        fused_content: str = ""
        if run_fusion:
            self._emit({"type": "phase_start", "phase": "B"})
            for domain in TASK_DOMAINS:
                elo_dom = domain_elo.get(domain, {})
                non_baseline = {n: r for n, r in elo_dom.items() if not n.startswith("baseline")}
                if len(non_baseline) >= 2:
                    top_two = self._top_k_skills(elo_dom, k=2)
                    if len(top_two) == 2:
                        fp, fc = self._phase_fusion(
                            state=state,
                            skill_a_path=top_two[0],
                            skill_b_path=top_two[1],
                            skills=skills,
                            output_name=fused_output_name,
                            domain=domain,
                        )
                        if fused_path is None:
                            fused_path = fp
                            fused_content = fc
                else:
                    self._emit({
                        "type": "phase_b_skip",
                        "domain": domain,
                        "reason": "选手不足 2",
                    })
                    logger.info("阶段 B: 领域 %s 选手不足 2,跳过融合", domain)
            self._emit({"type": "phase_done", "phase": "B"})

        # ---- 阶段 C:按领域自改进 Bottom1 ----
        improvement: ImprovementReport | None = None
        bottom_skill: str | None = None
        if run_improvement:
            self._emit({"type": "phase_start", "phase": "C"})
            for domain in TASK_DOMAINS:
                elo_dom = domain_elo.get(domain, {})
                non_baseline = {n: r for n, r in elo_dom.items() if not n.startswith("baseline")}
                if non_baseline:
                    bottom = self._bottom_skill(elo_dom)
                    if bottom and bottom in skills:
                        imp = self._phase_improvement(
                            state=state,
                            skill_name=bottom,
                            skill_content=skills[bottom].content,
                            max_iterations=max_improve_iterations,
                            domain=domain,
                        )
                        if improvement is None:
                            improvement = imp
                            bottom_skill = bottom
                else:
                    self._emit({
                        "type": "phase_c_skip",
                        "domain": domain,
                        "reason": "无可用 skill",
                    })
            self._emit({"type": "phase_done", "phase": "C"})

        # ---- 阶段 D:总报告 ----
        self._emit({"type": "phase_start", "phase": "D"})
        report_path = self._phase_report(
            state=state,
            matches=matches,
            elo_state={},
            fused_path=fused_path,
            fused_content=fused_content,
            improvement=improvement,
            bottom_skill=bottom_skill,
            title=report_title,
            domain_elo=domain_elo,
        )
        self._emit({"type": "phase_done", "phase": "D", "report_path": str(report_path)})

        # 写入最终完成标记
        if not skip_state:
            state["status"] = "done"
            state["finished_at"] = _now_iso()
            self._save_state(state)

        # 扁平化 elo_state 兼容 FullReport
        flat_elo: dict[str, float] = {}
        for domain, ratings in domain_elo.items():
            for name, rating in ratings.items():
                flat_elo[f"{domain}::{name}"] = rating

        self._emit({
            "type": "cycle_complete",
            "match_count": len(matches),
            "report_path": str(report_path) if report_path else None,
            "fused_skill": str(fused_path) if fused_path else None,
            "bottom_skill": bottom_skill,
            "improvement_iterations": improvement.total_iterations if improvement else 0,
        })

        return FullReport(
            title=report_title,
            elo_state=flat_elo,
            matches=matches,
            fused_skill=fused_path,
            fused_content=fused_content,
            improvement=improvement,
            bottom_skill=bottom_skill,
            report_path=report_path,
            notes=state.get("notes", ""),
            raw_state=state,
        )

    # ============================================================
    # 融合入口(单独调用)
    # ============================================================

    def run_fusion(
        self,
        skill_a: str,
        skill_b: str,
        *,
        output: str | Path | None = None,
        task_context: str = "通用写作任务",
        judge_feedback: str = "",
        model: str = "deepseek-v4-pro",
        save_to: Path | None = None,
    ) -> Path:
        """融合两个 skill 并落盘为 .md,返回保存路径。

        Args:
            skill_a / skill_b: skill 文件路径(.md)。
            output: 输出文件名(只是文件名,不是完整路径);None 时按
                `<a>__<b>_fused.md` 自动生成。
            task_context: 任务上下文。
            judge_feedback: 评判反馈;可空。
            model: 模型名。
            save_to: 直接指定保存目录(覆盖默认 fused_dir);None 时用默认。

        Returns:
            落盘后的 .md 文件路径。
        """
        content_a = load_skill(skill_a)
        content_b = load_skill(skill_b)
        name_a = Path(skill_a).stem
        name_b = Path(skill_b).stem
        client = self._ensure_client()

        fused = fuse_skills(
            skill_a_content=content_a,
            skill_a_name=name_a,
            skill_b_content=content_b,
            skill_b_name=name_b,
            task_context=task_context,
            judge_feedback=judge_feedback,
            model=model,
            client=client,
        )

        target_dir = Path(save_to) if save_to else self._fused_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        if output is None:
            output = f"{name_a}__{name_b}_fused.md"
        out_path = target_dir / output
        out_path.write_text(fused, encoding="utf-8")
        logger.info("run_fusion: 写入 %s (len=%d)", out_path, len(fused))
        return out_path

    # ============================================================
    # 自改进入口(单独调用)
    # ============================================================

    def run_self_improvement(
        self,
        skill_path: str | Path,
        *,
        max_iterations: int = 3,
        target_elo_delta: float = 20.0,
        evaluator: Evaluator | None = None,
        model: str = "deepseek-v4-pro",
        save_to: Path | None = None,
    ) -> ImprovementReport:
        """对单个 skill 运行自改进循环并保存最终版本,返回 ImprovementReport。

        Args:
            skill_path: skill 文件路径。
            max_iterations: 最大循环次数。
            target_elo_delta: 单轮 Elo 提升目标。
            evaluator: 注入的 evaluator;None 时使用默认占位(返回 1500, [])。
            model: 模型名。
            save_to: 最终版本的落盘目录;None 时用 improved_dir/。

        Returns:
            ImprovementReport(每轮 skill 版本 + Elo 快照)。
        """
        path = Path(skill_path)
        skill_content = load_skill(path)
        skill_name = path.stem
        client = self._ensure_client()

        report = run_improvement_cycle(
            skill_name=skill_name,
            skill_content=skill_content,
            max_iterations=max_iterations,
            target_elo_delta=target_elo_delta,
            evaluator=evaluator,
            model=model,
            client=client,
        )

        if report.steps:
            target_dir = Path(save_to) if save_to else self._improved_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            out_path = target_dir / f"{skill_name}.v{len(report.steps)}.md"
            out_path.write_text(
                report.steps[-1].skill_version, encoding="utf-8"
            )
            logger.info(
                "run_self_improvement: 写入 %s (steps=%d, final_elo=%.1f)",
                out_path,
                report.total_iterations,
                report.final_elo,
            )

        return report

    # ============================================================
    # 工具方法
    # ============================================================

    def list_skills(self, skills_dir: Path | None = None) -> list[str]:
        """列出 skills_dir(.md 文件名去后缀)。默认用 SKILLS_DIR。"""
        from .runner import list_available_skills

        return list_available_skills(skills_dir or SKILLS_DIR)

    def list_tasks(self) -> list[Path]:
        """列出固定任务集下所有 yaml。"""
        if not TASKS_DIR.exists():
            return []
        return sorted(TASKS_DIR.glob("*.yaml"))

    def save_elo(self, state: dict[str, float]) -> Path:
        """显式保存 Elo 状态;返回实际写入的文件路径。"""
        path = self._elo_state_path or ELO_STATE_FILE
        return save_state(state, path)

    def reset_state(self) -> None:
        """强制删除 state.json(下次 run_full_cycle 将重新跑)。"""
        if self._state_path.exists():
            self._state_path.unlink()
        logger.info("reset_state: 已删除 %s", self._state_path)

    def regenerate_report(
        self,
        *,
        title: str = "Skill 竞技场 · Elo 报告(重新生成)",
    ) -> Path:
        """从 reports/elo_state.json + 已缓存的 runs 重新生成 Markdown 报告。

        注意:本方法只重组"阶段 D",不重跑阶段 A/B/C;若想全量重跑请用
        `reset_state()` + `run_full_cycle(skip_state=False)`。
        """
        ensure_reports_dir()
        elo_state = self._read_elo_state()
        matches = self._reconstruct_matches_from_cache()
        path = generate_report(
            matches,
            elo_state,
            output_path=REPORTS_DIR / f"report_{_ts()}.md",
            title=title,
        )
        logger.info("regenerate_report: 写入 %s", path)
        return path

    # ============================================================
    # 内部:skill / task 加载
    # ============================================================

    def _load_skills(self, skill_paths: Iterable[str]) -> dict[str, SkillEntry]:
        """加载并校验所有 skill;以 stem 为 key。"""
        skills: dict[str, SkillEntry] = {}
        for p in skill_paths:
            path = Path(p)
            if not path.is_file() or path.suffix != ".md":
                logger.warning("run_full_cycle: 跳过无效 skill 路径 %s", p)
                continue
            try:
                entry = load_skill_entry(path)
            except Exception as exc:
                logger.warning("run_full_cycle: 加载 skill %s 失败: %s", p, exc)
                continue
            if not entry.content.strip():
                logger.warning("run_full_cycle: 跳过空 skill %s", p)
                continue
            skills[entry.name] = entry
        if not skills:
            raise ValueError("没有任何可用的 skill(全部路径无效或文件为空)")
        return skills

    def _resolve_tasks(
        self,
        *,
        task_source: str,
        auto_categories: list[str] | None,
        auto_per_category: int,
    ) -> list[dict[str, Any]]:
        """根据 task_source 加载任务,统一转为 list[dict]。

        转换格式: {id, category, prompt, difficulty, reference, source}
        """
        all_tasks: list[dict[str, Any]] = []
        if task_source in ("fixed", "hybrid"):
            for path in sorted(TASKS_DIR.glob("*.yaml")) if TASKS_DIR.exists() else []:
                for t in _load_fixed_tasks(path):
                    t = dict(t)
                    t["source"] = "fixed"
                    all_tasks.append(t)

        if task_source in ("auto", "hybrid"):
            cats = auto_categories if auto_categories else list(TASK_DOMAINS)
            auto_tasks = self._generate_auto_tasks(
                categories=cats, per_category=auto_per_category
            )
            for t in auto_tasks:
                item = t.model_dump()
                item["source"] = "auto"
                all_tasks.append(item)

        if task_source == "hybrid":
            # 用 jaccard 去重(轻量、零依赖)
            try:
                deduped = self._dedupe_tasks(all_tasks)
                if len(deduped) < len(all_tasks):
                    logger.info(
                        "_resolve_tasks(hybrid): 去重 %d -> %d",
                        len(all_tasks),
                        len(deduped),
                    )
                all_tasks = deduped
            except Exception as exc:  # noqa: BLE001
                logger.warning("_resolve_tasks: 去重失败,保留原列表: %s", exc)

        if not all_tasks:
            raise ValueError(
                f"task_source={task_source!r} 下没有任何任务(检查 tasks/ 与 TaskGenerator)"
            )
        return all_tasks

    def _generate_auto_tasks(
        self,
        *,
        categories: list[str],
        per_category: int,
    ) -> list[Task]:
        """调 v4-pro 动态生成任务。失败时返回空列表并打 warning(不阻塞主流程)。"""
        client = self._ensure_client()
        gen = TaskGenerator(client=client)
        dedup = TaskDeduplicator()
        results: list[Task] = []
        for cat in categories:
            try:
                batch = gen.generate_batch(
                    category=cat, count=per_category, difficulty="medium"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_generate_auto_tasks(%s): 模型调用失败: %s", cat, exc
                )
                batch = []
            for t in batch:
                if not dedup.is_duplicate(t, results):
                    results.append(t)
        # 同步落盘(便于回放 / 审计)
        try:
            TASKS_AUTO_DIR.mkdir(parents=True, exist_ok=True)
            payload = [t.model_dump() for t in results]
            (TASKS_AUTO_DIR / f"batch_{_ts()}.yaml").write_text(
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("_generate_auto_tasks: 落盘失败: %s", exc)
        return results

    @staticmethod
    def _dedupe_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """对 dict 任务做 jaccard 去重(基于 prompt)。"""
        seen_prompts: list[str] = []
        out: list[dict[str, Any]] = []
        for t in tasks:
            prompt = (t.get("prompt") or "").strip()
            if not prompt:
                continue
            is_dup = False
            for prev in seen_prompts:
                if jaccard_similarity(prompt, prev) >= 0.85:
                    is_dup = True
                    break
            if not is_dup:
                seen_prompts.append(prompt)
                out.append(t)
        return out

    # ============================================================
    # 内部:阶段 A
    # ============================================================

    def _phase_arena(
        self,
        *,
        state: dict[str, Any],
        skills: dict[str, SkillEntry],
        tasks: list[dict[str, Any]],
        rounds_per_pair: int,
    ) -> list[MatchResult]:
        """阶段 A:按领域分组竞技,每个领域内两两配对 → judge → 更新 Elo。"""
        if state["phases"].get("A", {}).get("status") == "done":
            logger.info("阶段 A 已 done,跳过(从缓存恢复 matches)")
            return self._reconstruct_matches_from_cache()

        # Resume: 必须保留上一轮已记录的 match_id 去重集合与计数,否则
        # 整个阶段 A 会被重跑(judge 重复调用 → Elo 重复计算)。原实现直接
        # 重新赋值 state["phases"]["A"] 会丢掉 recorded_ids,是 resume 路径的 bug。
        prev_A = state["phases"].get("A", {})
        state["phases"]["A"] = {
            "status": "running",
            "started_at": _now_iso(),
            "matches": prev_A.get("matches", 0),
            "recorded_ids": prev_A.get("recorded_ids", []),
        }
        self._save_state(state)

        domain_elo: dict[str, dict[str, float]] = self._read_domain_elo_state()
        client = self._ensure_client()
        self._runs_cache_dir.mkdir(parents=True, exist_ok=True)

        all_matches: list[MatchResult] = []

        # 是否存在专用(非 general)skill —— 决定 domain 锚定规则
        has_specialized = any("general" not in s.domains for s in skills.values())

        # 预计算总比赛数(用于前端进度条)
        total_expected_matches = 0
        for _d in TASK_DOMAINS:
            _ds = {n: s for n, s in skills.items() if s.participates_in(_d)}
            _dt = [t for t in tasks if t.get("category", "unknown") == _d]
            if _dt and len(_ds) >= 1 and self._domain_is_active(_ds, has_specialized):
                _pairs = len(list(combinations(list(_ds.keys()) + [f"baseline_{_d}"], 2)))
                total_expected_matches += len(_dt) * _pairs * rounds_per_pair
        self._emit({
            "type": "phase_a_plan",
            "total_matches": total_expected_matches,
            "rounds_per_pair": rounds_per_pair,
        })

        for domain in TASK_DOMAINS:
            domain_skills = {
                n: s for n, s in skills.items() if s.participates_in(domain)
            }
            domain_tasks = [t for t in tasks if t.get("category", "unknown") == domain]

            if len(domain_skills) < 1 or not domain_tasks:
                self._emit({
                    "type": "phase_a_domain_skip",
                    "domain": domain,
                    "skill_count": len(domain_skills),
                    "task_count": len(domain_tasks),
                    "reason": "无任务或无参与 skill",
                })
                logger.info(
                    "阶段 A: 领域 %s 跳过(skills=%d, tasks=%d)",
                    domain, len(domain_skills), len(domain_tasks),
                )
                continue

            # 锚定隔离:存在专用 skill 时,该 domain 必须有专用 skill 参与;
            # general skill 不能独自开启一个 domain(避免跨域泄漏)
            if not self._domain_is_active(domain_skills, has_specialized):
                self._emit({
                    "type": "phase_a_domain_skip",
                    "domain": domain,
                    "skill_count": len(domain_skills),
                    "task_count": len(domain_tasks),
                    "reason": "无专用 skill 锚定(仅 general),跳过以隔离 domain",
                })
                logger.info(
                    "阶段 A: 领域 %s 跳过(仅 general 参与,无专用 skill 锚定)",
                    domain,
                )
                continue

            baseline_name = f"baseline_{domain}"
            domain_elo.setdefault(domain, {})
            for name in domain_skills:
                domain_elo[domain].setdefault(name, 1500.0)
            domain_elo[domain].setdefault(baseline_name, 1500.0)

            names = list(domain_skills.keys()) + [baseline_name]
            domain_pairs = list(combinations(names, 2))
            domain_total_matches = len(domain_pairs) * rounds_per_pair

            self._emit({
                "type": "phase_a_domain_start",
                "domain": domain,
                "skill_count": len(domain_skills),
                "task_count": len(domain_tasks),
                "domain_total_matches": domain_total_matches,
                "total_matches": total_expected_matches,
            })

            for task in domain_tasks:
                tid = task["id"]
                tprompt = task["prompt"]

                outputs: dict[str, str] = {}
                for name in names:
                    cache_path = self._cache_path(tid, name)
                    if cache_path.exists() and cache_path.read_text(encoding="utf-8").strip():
                        outputs[name] = cache_path.read_text(encoding="utf-8")
                        self._emit({
                            "type": "phase_a_skill_exec",
                            "domain": domain,
                            "task_id": tid,
                            "skill": name,
                            "cache_hit": True,
                        })
                        continue

                    if name == baseline_name:
                        skill_content = None
                        skill_name_for_run = None
                    else:
                        skill_content = domain_skills[name].content
                        skill_name_for_run = name

                    self._emit({
                        "type": "phase_a_skill_exec",
                        "domain": domain,
                        "task_id": tid,
                        "skill": name,
                    })

                    # 流式执行:优先使用 execute_stream 实现实时输出推送
                    if hasattr(client, "execute_stream"):
                        _buf: list[str] = [""]  # mutable container for closure

                        def _on_chunk(text: str, _tid=tid, _name=name, _domain=domain) -> None:
                            _buf[0] += text
                            self._emit({
                                "type": "skill_output_chunk",
                                "domain": _domain,
                                "task_id": _tid,
                                "skill": _name,
                                "text": text,
                                "accumulated": _buf[0],
                            })

                        self._emit({
                            "type": "skill_output_start",
                            "domain": domain,
                            "task_id": tid,
                            "skill": name,
                        })
                        messages: list[dict[str, str]] = []
                        if skill_content is not None and skill_content.strip():
                            messages.append({"role": "system", "content": skill_content})
                        messages.append({"role": "user", "content": tprompt})
                        result = client.execute_stream(messages, on_chunk=_on_chunk)
                        run_content = result.content
                        self._emit({
                            "type": "skill_output_done",
                            "domain": domain,
                            "task_id": tid,
                            "skill": name,
                            "output": run_content,
                            "tokens": result.total_tokens,
                            "cache_hit": result.cache_hit_tokens > 0,
                            "cache_hit_tokens": result.cache_hit_tokens,
                            "cache_miss_tokens": result.cache_miss_tokens,
                        })
                        run = RunOutput(
                            skill_name=skill_name_for_run,
                            task=tprompt,
                            content=run_content,
                            tokens=result.total_tokens,
                            model=result.model,
                        )
                    else:
                        run = run_with_skill(
                            task=tprompt,
                            skill_content=skill_content,
                            client=client,
                            skill_name=skill_name_for_run,
                        )
                    outputs[name] = run.content
                    cache_path.write_text(run.content, encoding="utf-8")

                for a, b in domain_pairs:
                    for round_idx in range(1, rounds_per_pair + 1):
                        match_id = f"{domain}#{tid}#{a}__{b}#r{round_idx}"
                        if self._match_already_recorded(state, match_id):
                            continue
                        v: Verdict = compare(
                            task=tprompt,
                            output_a=outputs[a],
                            output_b=outputs[b],
                            skill_a=a,
                            skill_b=b,
                            client=client,
                        )
                        score = v.to_score()
                        elo_dom = domain_elo[domain]
                        r_a, r_b = elo_dom.get(a, 1500.0), elo_dom.get(b, 1500.0)
                        new_a, new_b = update_rating(r_a, r_b, score)
                        elo_dom[a] = new_a
                        elo_dom[b] = new_b

                        rec = MatchResult(
                            match_id=match_id,
                            timestamp=_now_iso(),
                            task_id=tid,
                            task_prompt=tprompt,
                            skill_a=a,
                            skill_b=b,
                            verdict=v,
                            output_a=outputs[a],
                            output_b=outputs[b],
                            domain=domain,
                        )
                        all_matches.append(rec)
                        _append_match_log(rec, MATCHES_LOG)
                        state["phases"]["A"].setdefault("recorded_ids", []).append(match_id)
                        state["phases"]["A"]["matches"] = len(all_matches)
                        self._save_state(state)

                        self._emit({
                            "type": "phase_a_match",
                            "domain": domain,
                            "match_id": match_id,
                            "task_id": tid,
                            "skill_a": a,
                            "skill_b": b,
                            "winner": v.winner,
                            "score_a": v.total_score("A"),
                            "score_b": v.total_score("B"),
                            "reasoning": v.reasoning,
                            "elo_a": new_a,
                            "elo_b": new_b,
                            "elo_delta_a": new_a - r_a,
                            "elo_delta_b": new_b - r_b,
                            "match_index": len(all_matches),
                            "total_matches": total_expected_matches,
                        })

            self._save_domain_elo_state(domain_elo)
            self._emit({
                "type": "phase_a_domain_done",
                "domain": domain,
                "elo_snapshot": dict(domain_elo[domain]),
            })

        # 收尾
        state["phases"]["A"]["status"] = "done"
        state["phases"]["A"]["finished_at"] = _now_iso()
        self._save_state(state)
        self._save_domain_elo_state(domain_elo)
        return all_matches

    def _match_already_recorded(
        self, state: dict[str, Any], match_id: str
    ) -> bool:
        recorded = state["phases"].get("A", {}).get("recorded_ids", [])
        return match_id in recorded

    def _cache_path(self, task_id: str, skill_name: str) -> Path:
        safe = skill_name.replace("/", "_").replace("\\", "_")
        return self._runs_cache_dir / f"{task_id}__{safe}.txt"

    # ============================================================
    # 内部:阶段 B / C / D
    # ============================================================

    def _phase_fusion(
        self,
        *,
        state: dict[str, Any],
        skill_a_path: str,
        skill_b_path: str,
        skills: dict[str, SkillEntry],
        output_name: str | None,
        domain: str = "",
    ) -> tuple[Path, str]:
        """阶段 B:取某领域 Top2 → 融合 → 落盘。返回 (path, content)。"""
        domain_prefix = f"{domain}_" if domain else ""
        phase_key = f"B_{domain}" if domain else "B"
        phase_b = state["phases"].setdefault(phase_key, {})
        if phase_b.get("status") == "done" and phase_b.get("output_path"):
            p = Path(phase_b["output_path"])
            if p.exists():
                logger.info("阶段 B(%s) 已 done,跳过(读缓存 %s)", domain, p)
                return p, p.read_text(encoding="utf-8")

        phase_b["status"] = "running"
        phase_b["started_at"] = _now_iso()
        state["phases"][phase_key] = phase_b
        self._save_state(state)

        name_a = Path(skill_a_path).stem if any(c in skill_a_path for c in ("/", "\\")) else skill_a_path
        name_b = Path(skill_b_path).stem if any(c in skill_b_path for c in ("/", "\\")) else skill_b_path

        # 构造轻量 judge_feedback(基于领域 Elo 差)
        domain_elo = self._read_domain_elo_state()
        elo_dom = domain_elo.get(domain, {})
        elo_a = elo_dom.get(name_a, 1500.0)
        elo_b = elo_dom.get(name_b, 1500.0)
        judge_feedback = (
            f"当前 Elo:A({name_a})={elo_a:.1f}, B({name_b})={elo_b:.1f}。"
            f"保留 A 的强项({name_a} 在最近对战中的优势),"
            f"保留 B 的强项({name_b} 的优势),同时去掉各自的弱项。"
        )

        client = self._ensure_client()
        self._fused_dir.mkdir(parents=True, exist_ok=True)
        target_name = output_name or f"{domain_prefix}{name_a}__{name_b}_fused.md"
        target_path = self._fused_dir / target_name

        self._emit({
            "type": "phase_b_fuse_start",
            "domain": domain,
            "skill_a": name_a,
            "skill_b": name_b,
            "elo_a": elo_a,
            "elo_b": elo_b,
        })

        try:
            fused = fuse_skills(
                skill_a_content=skills[name_a].content,
                skill_a_name=name_a,
                skill_b_content=skills[name_b].content,
                skill_b_name=name_b,
                task_context=(
                    "Skill 竞技场端到端流程:对 Elo 排名前二的两个 skill 做融合,"
                    "取长补短,生成新版本 skill 文档。"
                ),
                judge_feedback=judge_feedback,
                model="deepseek-v4-pro",
                client=client,
            )
        except Exception as exc:  # noqa: BLE001
            phase_b["status"] = "failed"
            phase_b["error"] = repr(exc)
            self._save_state(state)
            self._emit({
                "type": "phase_b_fuse_failed",
                "domain": domain,
                "error": repr(exc),
            })
            raise

        target_path.write_text(fused, encoding="utf-8")
        phase_b["status"] = "done"
        phase_b["output_path"] = str(target_path)
        phase_b["finished_at"] = _now_iso()
        state["phases"]["B"] = phase_b
        self._save_state(state)
        self._emit({
            "type": "phase_b_fuse_done",
            "domain": domain,
            "skill_a": name_a,
            "skill_b": name_b,
            "output_path": str(target_path),
            "output_length": len(fused),
        })
        return target_path, fused

    def _phase_improvement(
        self,
        *,
        state: dict[str, Any],
        skill_name: str,
        skill_content: str,
        max_iterations: int,
        domain: str = "",
    ) -> ImprovementReport:
        """阶段 C:对某领域 Bottom1 skill 跑自改进循环。"""
        phase_key = f"C_{domain}" if domain else "C"
        phase_c = state["phases"].setdefault(phase_key, {})
        if phase_c.get("status") == "done" and phase_c.get("skill_name") == skill_name:
            logger.info("阶段 C(%s) 已 done for %s,跳过", domain, skill_name)
            self._emit({
                "type": "phase_c_skip_cached",
                "domain": domain,
                "skill": skill_name,
            })
            return _ImprovementReport_cached(state, skill_name)

        phase_c["status"] = "running"
        phase_c["skill_name"] = skill_name
        phase_c["started_at"] = _now_iso()
        state["phases"][phase_key] = phase_c

        self._emit({
            "type": "phase_c_improve_start",
            "domain": domain,
            "skill": skill_name,
            "max_iterations": max_iterations,
        })
        self._save_state(state)

        # 注入真实 evaluator:跑 N 场 vs baseline_{domain},更新领域 Elo,收集 weaknesses
        domain_elo = self._read_domain_elo_state()
        client = self._ensure_client()
        cache = self._runs_cache_dir
        cache.mkdir(parents=True, exist_ok=True)
        baseline_name = f"baseline_{domain}" if domain else "baseline"

        def evaluator(s_content: str, s_name: str) -> tuple[float, list[str]]:
            return self._improvement_evaluator(
                skill_content=s_content,
                skill_name=s_name,
                domain_elo=domain_elo,
                domain=domain,
                baseline_name=baseline_name,
                client=client,
                cache_dir=cache,
            )

        def _on_iteration(
            iteration: int,
            elo_before: float,
            elo_after: float,
            elo_delta: float,
            weaknesses: tuple[str, ...],
            iter_converged: bool,
            total_iterations: int,
        ) -> None:
            self._emit({
                "type": "phase_c_iteration",
                "domain": domain,
                "skill": skill_name,
                "iteration": iteration,
                "elo_before": elo_before,
                "elo_after": elo_after,
                "elo_delta": elo_delta,
                "weaknesses_count": len(weaknesses),
                "converged": iter_converged,
                "total_iterations": total_iterations,
            })

        report = run_improvement_cycle(
            skill_name=skill_name,
            skill_content=skill_content,
            max_iterations=max_iterations,
            target_elo_delta=20.0,
            evaluator=evaluator,
            model="deepseek-v4-pro",
            client=client,
            on_iteration=_on_iteration,
        )

        # 落盘最终 skill 版本
        if report.steps:
            self._improved_dir.mkdir(parents=True, exist_ok=True)
            out_path = self._improved_dir / f"{skill_name}.v{len(report.steps)}.md"
            out_path.write_text(
                report.steps[-1].skill_version, encoding="utf-8"
            )
            phase_c["output_path"] = str(out_path)
        phase_c["status"] = "done"
        phase_c["finished_at"] = _now_iso()
        phase_c["total_iterations"] = report.total_iterations
        phase_c["converged"] = report.converged
        phase_c["final_elo"] = report.final_elo
        state["phases"][phase_key] = phase_c
        self._save_state(state)
        self._save_domain_elo_state(domain_elo)
        self._emit({
            "type": "phase_c_improve_done",
            "domain": domain,
            "skill": skill_name,
            "total_iterations": report.total_iterations,
            "final_elo": report.final_elo,
            "converged": report.converged,
            "output_path": phase_c.get("output_path"),
        })
        return report

    def _improvement_evaluator(
        self,
        *,
        skill_content: str,
        skill_name: str,
        domain_elo: dict[str, dict[str, float]],
        domain: str,
        baseline_name: str,
        client: DeepSeekClient,
        cache_dir: Path,
    ) -> tuple[float, list[str]]:
        """生产 evaluator:在领域固定任务子集上跑 vs baseline → 更新 Elo + 收集 weaknesses。"""
        weak: list[str] = []
        wins = 0
        losses = 0
        draws = 0
        elo_dom = domain_elo.setdefault(domain, {})
        picked: list[dict[str, Any]] = []
        for path in sorted(TASKS_DIR.glob("*.yaml")) if TASKS_DIR.exists() else []:
            for t in _load_fixed_tasks(path):
                if t.get("category", "unknown") == domain or not domain:
                    picked.append(dict(t))
                    if len(picked) >= 2:
                        break
            if len(picked) >= 2:
                break
        if not picked:
            return (elo_dom.get(skill_name, 1500.0), weak)

        for task in picked:
            tid = task["id"]
            tprompt = task["prompt"]
            cp_self = cache_dir / f"{tid}__{skill_name}.txt"
            cp_base = cache_dir / f"{tid}__{baseline_name}.txt"
            if not cp_self.exists():
                run = run_with_skill(
                    task=tprompt,
                    skill_content=skill_content,
                    client=client,
                    skill_name=skill_name,
                )
                cp_self.write_text(run.content, encoding="utf-8")
            if not cp_base.exists():
                run = run_with_skill(
                    task=tprompt,
                    skill_content=None,
                    client=client,
                    skill_name=None,
                )
                cp_base.write_text(run.content, encoding="utf-8")
            content_self = cp_self.read_text(encoding="utf-8")
            content_base = cp_base.read_text(encoding="utf-8")

            try:
                v = compare(
                    task=tprompt,
                    output_a=content_self,
                    output_b=content_base,
                    skill_a=skill_name,
                    skill_b=baseline_name,
                    client=client,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("improvement_evaluator: compare 失败: %s", exc)
                continue

            r_a, r_b = update_rating(
                elo_dom.get(skill_name, 1500.0),
                elo_dom.get(baseline_name, 1500.0),
                v.to_score(),
            )
            elo_dom[skill_name] = r_a
            elo_dom[baseline_name] = r_b

            if v.winner == "A":
                wins += 1
            elif v.winner == "B":
                losses += 1
            else:
                draws += 1

            # 简单 weaknesses 抽取:取 reason + scores 中较低维度
            weak.append(v.reasoning)
            scores_a = v.scores.get("A")
            if scores_a is not None:
                for dim in ("correctness", "completeness", "clarity", "creativity"):
                    val = getattr(scores_a, dim, None)
                    if val is not None and val < 6.0:
                        weak.append(f"{dim} 偏低({val:.1f})")

        return (elo_dom.get(skill_name, 1500.0), weak)

    def _phase_report(
        self,
        *,
        state: dict[str, Any],
        matches: list[MatchResult],
        elo_state: dict[str, float],
        fused_path: Path | None,
        fused_content: str,
        improvement: ImprovementReport | None,
        bottom_skill: str | None,
        title: str,
        domain_elo: dict[str, dict[str, float]] | None = None,
    ) -> Path:
        """阶段 D:生成 Markdown 总报告。"""
        ensure_reports_dir()
        report_path = generate_report(
            matches,
            elo_state,
            output_path=REPORTS_DIR / f"report_{_ts()}.md",
            title=title,
            domain_elo=domain_elo,
        )

        # 追加"阶段 B / C 摘要"小节
        appendix = _render_phase_bc_appendix(
            elo_state=elo_state,
            fused_path=fused_path,
            fused_content=fused_content,
            improvement=improvement,
            bottom_skill=bottom_skill,
        )
        if appendix:
            with report_path.open("a", encoding="utf-8") as f:
                f.write("\n\n")
                f.write(appendix)

        state["phases"]["D"] = {
            "status": "done",
            "started_at": _now_iso(),
            "finished_at": _now_iso(),
            "report_path": str(report_path),
        }
        self._save_state(state)
        logger.info("阶段 D: 报告写入 %s", report_path)
        return report_path

    # ============================================================
    # 内部:state 持久化
    # ============================================================

    def _new_state(
        self, *, skill_paths: Iterable[str], task_source: str
    ) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "created_at": _now_iso(),
            "status": "running",
            "skill_paths": list(skill_paths),
            "task_source": task_source,
            "phases": {
                "A": {"status": "pending"},
                "B": {"status": "pending"},
                "C": {"status": "pending"},
                "D": {"status": "pending"},
            },
            "notes": "",
        }

    def _load_state(
        self,
        skill_paths: Iterable[str],
        task_source: str,
    ) -> dict[str, Any]:
        if not self._state_path.exists():
            return self._new_state(
                skill_paths=skill_paths, task_source=task_source
            )
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "orchestrator state 损坏,重新创建: %s (%s)", self._state_path, exc
            )
            return self._new_state(
                skill_paths=skill_paths, task_source=task_source
            )
        if not isinstance(data, dict) or data.get("schema_version") != STATE_SCHEMA_VERSION:
            logger.info("orchestrator state schema 不匹配,重新创建")
            return self._new_state(
                skill_paths=skill_paths, task_source=task_source
            )
        # 补齐缺失 phase 字段
        for k in ("A", "B", "C", "D"):
            data["phases"].setdefault(k, {"status": "pending"})
        data.setdefault("status", "running")
        data["status"] = "running"  # 强制 running,允许恢复
        return data

    def _save_state(self, state: dict[str, Any]) -> None:
        ensure_reports_dir()
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        # 防御性:把 state 中的 Path / 不可序列化对象转 str
        sanitized = _sanitize_for_json(state)
        tmp = self._state_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(sanitized, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        tmp.replace(self._state_path)

    def _read_elo_state(self) -> dict[str, float]:
        path = self._elo_state_path or ELO_STATE_FILE
        return load_state(path)

    def _save_elo_state(self, ratings: Mapping[str, float]) -> None:
        path = self._elo_state_path or ELO_STATE_FILE
        save_state(dict(ratings), path)

    def _read_domain_elo_state(self) -> dict[str, dict[str, float]]:
        path = self._elo_state_path or ELO_STATE_FILE
        return load_domain_state(path)

    def _save_domain_elo_state(self, domain_elo: dict[str, dict[str, float]]) -> None:
        path = self._elo_state_path or ELO_STATE_FILE
        save_domain_state(domain_elo, path)

    # ============================================================
    # 内部:辅助
    # ============================================================

    def _reconstruct_matches_from_cache(self) -> list[MatchResult]:
        """从 matches.jsonl 恢复 matches 列表(用于断点续跑和 regenerate_report)。"""
        return _read_match_log(MATCHES_LOG)

    @staticmethod
    def _domain_is_active(
        domain_skills: dict[str, SkillEntry],
        has_specialized: bool,
    ) -> bool:
        """domain 是否应激活(锚定规则)。

        - 全部选中 skill 都是 general(has_specialized=False) → 所有 domain 激活,
          general skill 可参与所有评测。
        - 否则(存在专用 skill)→ 该 domain 必须有至少一个专用(非 general)skill 参与,
          general skill 不能独自开启一个 domain,避免跨域泄漏。
        """
        if not has_specialized:
            return True
        return any("general" not in s.domains for s in domain_skills.values())

    @staticmethod
    def _top_k_skills(elo: Mapping[str, float], k: int) -> list[str]:
        """返回 Elo 排名前 k 的 skill 名(排除 baseline_*)。"""
        candidates = [(n, r) for n, r in elo.items() if not n.startswith("baseline")]
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [n for n, _ in candidates[:k]]

    @staticmethod
    def _bottom_skill(elo: Mapping[str, float]) -> str | None:
        """返回 Elo 最低的 skill 名(排除 baseline_*)。"""
        candidates = [(n, r) for n, r in elo.items() if not n.startswith("baseline")]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]

    def _ensure_client(self) -> DeepSeekClient:
        if self._client is None:
            self._client = DeepSeekClient()
        return self._client


# ============================================================
# 内部辅助
# ============================================================


def _load_fixed_tasks(path: Path) -> list[dict[str, Any]]:
    """从 YAML 文件加载任务,容错:返回 list[dict]。"""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:  # noqa: BLE001
        logger.warning("加载任务文件 %s 失败: %s", path, exc)
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if "id" not in item or "prompt" not in item:
            continue
        out.append(
            {
                "id": str(item["id"]),
                "category": str(item.get("category", "unknown")),
                "prompt": str(item["prompt"]),
                "difficulty": str(item.get("difficulty", "medium")),
                "reference": item.get("reference"),
            }
        )
    return out


def _serialize_match(rec: MatchResult) -> dict[str, Any]:
    """将 MatchResult 序列化为可 JSON 化的 dict。"""
    return {
        "match_id": rec.match_id,
        "timestamp": rec.timestamp,
        "task_id": rec.task_id,
        "task_prompt": rec.task_prompt,
        "skill_a": rec.skill_a,
        "skill_b": rec.skill_b,
        "verdict": {
            "winner": rec.verdict.winner,
            "scores": {
                "A": rec.verdict.scores["A"].model_dump(),
                "B": rec.verdict.scores["B"].model_dump(),
            },
            "reasoning": rec.verdict.reasoning,
        },
        "output_a": rec.output_a,
        "output_b": rec.output_b,
        "domain": rec.domain,
    }


def _deserialize_match(data: dict[str, Any]) -> MatchResult:
    """从 dict 反序列化 MatchResult。"""
    from .judge import DimensionScores
    v = data["verdict"]
    return MatchResult(
        match_id=data["match_id"],
        timestamp=data["timestamp"],
        task_id=data["task_id"],
        task_prompt=data["task_prompt"],
        skill_a=data["skill_a"],
        skill_b=data["skill_b"],
        verdict=Verdict(
            winner=v["winner"],
            scores={
                "A": DimensionScores(**v["scores"]["A"]),
                "B": DimensionScores(**v["scores"]["B"]),
            },
            reasoning=v["reasoning"],
        ),
        output_a=data.get("output_a", ""),
        output_b=data.get("output_b", ""),
        domain=data.get("domain", ""),
    )


def _append_match_log(rec: MatchResult, log_path: Path) -> None:
    """追加一条 MatchResult 到 JSONL 文件。"""
    ensure_reports_dir()
    line = json.dumps(_sanitize_for_json(_serialize_match(rec)), ensure_ascii=False)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _read_match_log(log_path: Path) -> list[MatchResult]:
    """从 JSONL 文件读取所有 MatchResult。"""
    if not log_path.exists():
        return []
    results: list[MatchResult] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            results.append(_deserialize_match(data))
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning("跳过损坏的 match 记录: %s", exc)
    return results


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _sanitize_for_json(obj: Any) -> Any:
    """递归把 Path / 非 dict 对象转 str,确保 json.dumps 不爆。"""
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _ImprovementReport_cached(
    state: dict[str, Any], skill_name: str
) -> ImprovementReport:
    """从 state 还原一个最小的 ImprovementReport(用于断点续跑)。"""
    from .self_improve import ImprovementReport, ImprovementStep

    phase_c = state["phases"].get("C", {})
    total = int(phase_c.get("total_iterations", 0))
    final_elo = float(phase_c.get("final_elo", 1500.0))
    converged = bool(phase_c.get("converged", False))
    # 这里没有原始 step 文本,只构造空 steps
    return ImprovementReport(
        skill_name=skill_name,
        steps=tuple(
            ImprovementStep(
                iteration=i + 1,
                skill_version="(从 state 恢复,无原始文本)",
                elo_before=1500.0,
                elo_after=final_elo,
                elo_delta=0.0,
                weaknesses=(),
            )
            for i in range(total)
        ),
        final_elo=final_elo,
        converged=converged,
        total_iterations=total,
        notes="(从 state.json 恢复)",
    )


def _render_phase_bc_appendix(
    *,
    elo_state: dict[str, float],
    fused_path: Path | None,
    fused_content: str,
    improvement: ImprovementReport | None,
    bottom_skill: str | None,
) -> str:
    """渲染阶段 B / C 摘要(追加到 report 末尾)。"""
    lines: list[str] = ["## 阶段 B · 融合", ""]
    if fused_path and fused_path.exists():
        lines.append(f"- 融合产物:`{fused_path}`")
        lines.append(f"- Top2 skill:{_format_top2(elo_state)}")
        lines.append("")
        lines.append("### 融合产物预览")
        lines.append("")
        lines.append("```markdown")
        lines.append(fused_content[:1200] + ("\n..." if len(fused_content) > 1200 else ""))
        lines.append("```")
    else:
        lines.append("- (未执行或失败)")

    lines.append("")
    lines.append("## 阶段 C · 自改进")
    lines.append("")
    if improvement and bottom_skill:
        lines.append(f"- 目标 skill(底部):`{bottom_skill}`")
        lines.append(f"- 迭代次数:**{improvement.total_iterations}**")
        lines.append(f"- 是否收敛:**{improvement.converged}**")
        lines.append(f"- 最终 Elo:{improvement.final_elo:.1f}")
        if improvement.notes:
            lines.append(f"- 备注:{improvement.notes}")
        if improvement.steps:
            lines.append("")
            lines.append("### 改进过程")
            lines.append("")
            lines.append("| Iter | Elo 前 | Elo 后 | Δ | 弱点数 |")
            lines.append("|:----:|------:|------:|--:|------:|")
            for s in improvement.steps:
                lines.append(
                    f"| {s.iteration} | {s.elo_before:.1f} | {s.elo_after:.1f} | "
                    f"{s.elo_delta:+.1f} | {len(s.weaknesses)} |"
                )
    else:
        lines.append("- (未执行或失败)")
    return "\n".join(lines)


def _format_top2(elo_state: Mapping[str, float]) -> str:
    items = [(n, r) for n, r in elo_state.items() if n != "baseline"]
    items.sort(key=lambda x: x[1], reverse=True)
    if len(items) < 2:
        return "(不足 2 个 skill)"
    return f"`{items[0][0]}` ({items[0][1]:.1f}) × `{items[1][0]}` ({items[1][1]:.1f})"


# 显式把 CompletionResult 暴露给 type checkers
_ = CompletionResult
_ = RunOutput

# 向后兼容:老代码用 `Report`,新代码用 `FullReport`;两者等价。
Report = FullReport

__all__ = [
    "ArenaOrchestrator",
    "FullReport",
    "Report",  # backward-compat alias
    "ORCHESTRATOR_STATE_FILE",
    "RUNS_CACHE_DIR",
    "FUSED_DIR",
    "IMPROVED_DIR",
    "STATE_SCHEMA_VERSION",
]
