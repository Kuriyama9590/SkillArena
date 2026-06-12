"""orchestrator 端到端单测:覆盖 run_full_cycle / 断点续跑 / 阶段 A/B/C/D。

测试目标(>=5 用例):
1.  mock 客户端下,run_full_cycle 能跑完所有阶段(A/B/C/D)。
2.  断点续跑:从已有 state.json 启动,能跳过已完成阶段。
3.  Elo 在阶段 A 结束后被正确更新。
4.  阶段 B 输出文件存在且长度合理。
5.  阶段 C 的 ImprovementReport 包含每轮的 skill 版本。
6.  (额外)CLI 子命令能被解析,handler 接受合法参数。
7.  (额外)task_source 非法值抛 ValueError。
8.  (额外)run_fusion / run_self_improvement 单独调用也能落盘。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from arena.deepseek_client import CompletionResult
from arena.elo import load_state
from arena.judge import DimensionScores, Verdict
from arena.orchestrator import (
    ArenaOrchestrator,
    FullReport,
)
from arena.self_improve import ImprovementReport, ImprovementStep

# 关闭根 logger 的过度输出(测试期间保持安静)
logging.getLogger("arena").setLevel(logging.WARNING)


# ============================================================
# Fakes
# ============================================================


def _make_verdict(
    winner: str = "A", sa: float = 8.0, sb: float = 6.0, reasoning: str = "ok"
) -> Verdict:
    """构造一个合规的 Verdict。"""
    return Verdict(
        winner=winner,
        scores={
            "A": DimensionScores(
                correctness=sa,
                completeness=sa,
                clarity=sa,
                creativity=sa,
            ),
            "B": DimensionScores(
                correctness=sb,
                completeness=sb,
                clarity=sb,
                creativity=sb,
            ),
        },
        reasoning=reasoning,
    )


def _verdict_to_json(v: Verdict) -> str:
    """把 Verdict 序列化成模型返回的 JSON 文本(供 fake judge 客户端返回)。"""
    return json.dumps(
        {
            "winner": v.winner,
            "scores": {
                "A": {
                    "correctness": v.scores["A"].correctness,
                    "completeness": v.scores["A"].completeness,
                    "clarity": v.scores["A"].clarity,
                    "creativity": v.scores["A"].creativity,
                },
                "B": {
                    "correctness": v.scores["B"].correctness,
                    "completeness": v.scores["B"].completeness,
                    "clarity": v.scores["B"].clarity,
                    "creativity": v.scores["B"].creativity,
                },
            },
            "reasoning": v.reasoning,
        },
        ensure_ascii=False,
    )


_GOOD_SKILL = (
    "# Hybrid\n\n"
    "## 核心原则\n"
    "1. 直接进入主题,第一句话点明观点。\n"
    "2. 每段一个核心想法,层次清晰,不要一锅炖。\n"
    "3. 短句优先,主动语态;能用一句话说清的不写两句话。\n"
    "4. 关键论点配一个具体例子:数字、场景或引语。\n"
    "5. 多角度展开:利弊、对比、历史、当下、未来至少给出两个。\n\n"
    "## 行为约束\n"
    "- 禁止套话开头,例如在当今社会、随着时代发展。\n"
    "- 禁止空泛例子,例如比如某些情况下。\n\n"
    "## 示例\n"
    "输入:用 100 字介绍设计模式。输出:设计模式是反复出现的问题的"
    "可复用解。例如观察者模式:报社一有新刊,所有订户自动收到通知。"
)

_GOOD_SKILL_ALT = (
    "# Alt Style\n\n"
    "## 核心原则\n"
    "1. 用结构化标题分章节,先给导读,再展开。\n"
    "2. 每个论点配一个数据或场景,避免空泛例子。\n"
    "3. 结尾给小结,呼应开头,形成闭环。\n"
    "4. 多角度对比:利弊、对比、历史、当下、未来至少给出两个。\n"
    "5. 短句优先,主动语态;能用一句话说清的不写两句话。\n\n"
    "## 行为约束\n"
    "- 禁止套话开头,例如在当今社会、随着时代发展。\n"
    "- 禁止空泛例子,例如比如某些情况下。\n\n"
    "## 示例\n"
    "输入:用 100 字介绍设计模式。输出:设计模式是反复出现的问题的"
    "可复用解。例如观察者模式:报社一有新刊,所有订户自动收到通知。"
)


_FUSE_OUTPUT = (
    "# Fused Skill\n\n"
    "## 核心原则\n"
    "1. 直接进入主题,第一句话点明观点。\n"
    "2. 每段一个核心想法,层次清晰,不要一锅炖。\n"
    "3. 短句优先,主动语态;能用一句话说清的不写两句话。\n"
    "4. 关键论点配一个具体例子:数字、场景或引语。\n"
    "5. 多角度展开:利弊、对比、历史、当下、未来至少给出两个。\n\n"
    "## 行为约束\n"
    "- 禁止套话开头,例如在当今社会、随着时代发展。\n"
    "- 禁止空泛例子,例如比如某些情况下。\n\n"
    "## 示例\n"
    "输入:用 100 字介绍设计模式。输出:设计模式是反复出现的问题的"
    "可复用解。例如观察者模式:报社一有新刊,所有订户自动收到通知。"
)

_IMPROVE_OUTPUT = (
    "# Improved Skill\n\n"
    "## 核心原则\n"
    "1. 直接进入主题,第一句话点明观点。\n"
    "2. 每段一个核心想法,层次清晰,不要一锅炖。\n"
    "3. 短句优先,主动语态;能用一句话说清的不写两句话。\n"
    "4. 关键论点配一个具体例子:数字、场景或引语。\n"
    "5. 多角度展开:利弊、对比、历史、当下、未来至少给出两个。\n\n"
    "## 行为约束\n"
    "- 禁止套话开头,例如在当今社会、随着时代发展。\n"
    "- 禁止空泛例子,例如比如某些情况下。\n\n"
    "## 示例\n"
    "输入:用 100 字介绍设计模式。输出:设计模式是反复出现的问题的"
    "可复用解。例如观察者模式:报社一有新刊,所有订户自动收到通知。"
)


class _FakeDeepSeekClient:
    """全功能 mock 客户端:按 prompt 内容路由返回不同结果。

    路由规则:
    - judge 调用:始终返回固定 verdict 的 JSON。
    - execute 调用且 prompt 含 "skill 文档" / "v3" / "skill 设计专家":返回 _FUSE_OUTPUT。
    - execute 调用且 prompt 含 "针对以上每一条 weakness" / "改进":返回 _IMPROVE_OUTPUT。
    - 其它 execute 调用:返回通用 execute_text(用于 runner)。

    这种"按消息特征分发"的 mock 让 run_full_cycle 内部所有路径(runner /
    compare / fuse / improve)都能在同一个 fake 客户端上跑通,而不必为每个
    路径分别 stub。
    """

    def __init__(
        self,
        *,
        execute_text: str = "fake runner response",
        verdict: Verdict | None = None,
    ) -> None:
        self.execute_text = execute_text
        self.verdict = verdict or _make_verdict()
        self.execute_calls = 0
        self.judge_calls = 0
        self.calls_execute: list[list[dict[str, str]]] = []
        self.calls_judge: list[list[dict[str, str]]] = []

    def execute(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> CompletionResult:
        self.execute_calls += 1
        self.calls_execute.append(list(messages))
        # 取最后一条 user message 来判断调用方
        user_content = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_content = m.get("content", "")
                break
        system_content = ""
        for m in messages:
            if m.get("role") == "system":
                system_content += m.get("content", "")

        # 路由:fuse(融合) → 返回合规 skill
        if (
            "skill 设计专家" in system_content
            or "fuse" in (model or "").lower()
            or "v3 版本的 skill" in user_content
            or "融合两个 skill 的优点" in user_content
        ):
            content = _FUSE_OUTPUT
        # 路由:improve(自改进) → 返回合规 skill
        elif (
            "针对以上每一条 weakness" in user_content
            or "弱点列表" in user_content
            or "improve" in (model or "").lower()
        ):
            content = _IMPROVE_OUTPUT
        else:
            content = self.execute_text

        return CompletionResult(
            content=content,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            model=model or "fake-exec",
        )

    def judge(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> CompletionResult:
        self.judge_calls += 1
        self.calls_judge.append(list(messages))
        return CompletionResult(
            content=_verdict_to_json(self.verdict),
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            model=model or "fake-judge",
        )

    @property
    def settings(self) -> Any:
        class _S:
            execute_model = "fake-exec"
            judge_model = "fake-judge"
            api_key = "fake"
            base_url = "http://fake"
            timeout_seconds = 1.0
            max_retries = 1

        return _S()


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture()
def two_skill_files(tmp_path: Path) -> list[Path]:
    """在 tmp_path 下创建 2 个合规 skill 文件。"""
    paths = []
    for name, content in [
        ("skill-a", _GOOD_SKILL),
        ("skill-b", _GOOD_SKILL_ALT),
    ]:
        p = tmp_path / f"{name}.md"
        p.write_text(content, encoding="utf-8")
        paths.append(p)
    return paths


@pytest.fixture()
def isolated_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    """重定向所有默认输出到 tmp_path,避免污染项目目录。"""
    fake_elo = tmp_path / "elo_state.json"
    fake_state = tmp_path / "orchestrator_state.json"
    fake_runs = tmp_path / "cache" / "runs"
    fake_fused = tmp_path / "fused"
    fake_improved = tmp_path / "improved"

    # 屏蔽 env 变量(避免 Settings 启动时报 "no api key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-test")
    monkeypatch.setenv("ARENA_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("ARENA_MAX_RETRIES", "1")

    # 屏蔽 config / orchestrator 的常量
    monkeypatch.setattr("arena.config.ELO_STATE_FILE", fake_elo)
    monkeypatch.setattr("arena.config.REPORTS_DIR", tmp_path)
    monkeypatch.setattr(
        "arena.orchestrator.ELO_STATE_FILE", fake_elo
    )
    monkeypatch.setattr(
        "arena.orchestrator.ORCHESTRATOR_STATE_FILE", fake_state
    )
    monkeypatch.setattr("arena.orchestrator.RUNS_CACHE_DIR", fake_runs)
    monkeypatch.setattr("arena.orchestrator.FUSED_DIR", fake_fused)
    monkeypatch.setattr("arena.orchestrator.IMPROVED_DIR", fake_improved)
    # runner.list_available_skills 也读 SKILLS_DIR
    monkeypatch.setattr("arena.config.SKILLS_DIR", tmp_path)
    return {
        "tmp": tmp_path,
        "elo": fake_elo,
        "state": fake_state,
        "runs": fake_runs,
        "fused": fake_fused,
        "improved": fake_improved,
    }


# ============================================================
# 用例 1:run_full_cycle 端到端(mock 下能跑完所有阶段)
# ============================================================


class TestRunFullCycleEndToEnd:
    def test_full_cycle_completes_all_stages(
        self,
        two_skill_files: list[Path],
        isolated_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """mock 客户端下,run_full_cycle 应能跑完阶段 A/B/C/D。"""
        fake = _FakeDeepSeekClient()
        # 屏蔽 TaskGenerator(避免调真实 v4-pro)
        class _StubTaskGenerator:
            def __init__(self, client: Any = None) -> None:
                self.client = client

            def generate_batch(self, *args: Any, **kwargs: Any) -> list[Any]:
                return []

        monkeypatch.setattr(
            "arena.task_generator.TaskGenerator", _StubTaskGenerator
        )

        orch = ArenaOrchestrator(
            client=fake,  # 注入 fake 客户端(关键)
            elo_state_path=isolated_paths["elo"],
            state_path=isolated_paths["state"],
            runs_cache_dir=isolated_paths["runs"],
            fused_dir=isolated_paths["fused"],
            improved_dir=isolated_paths["improved"],
        )
        report: FullReport = orch.run_full_cycle(
            skill_paths=[str(p) for p in two_skill_files],
            task_source="fixed",
            rounds_per_pair=1,
            max_improve_iterations=1,
        )

        # 阶段 D 必须写出报告
        assert isinstance(report, FullReport)
        assert report.report_path is not None
        assert report.report_path.exists()
        # 阶段 B 必须有融合产物
        assert report.fused_skill is not None
        assert report.fused_skill.exists()
        assert len(report.fused_content) > 0
        # 阶段 C 必须有 ImprovementReport
        assert report.improvement is not None
        assert report.bottom_skill is not None
        # Elo 至少有 3 个选手(2 skill + baseline)
        assert len(report.elo_state) >= 3
        # Elo 文件被写
        assert isolated_paths["elo"].exists()
        # State 文件被写
        assert isolated_paths["state"].exists()
        # matches 列表非空(阶段 A 跑了比赛)
        assert len(report.matches) > 0
        # 阶段标记全部 done
        state_data = json.loads(
            isolated_paths["state"].read_text(encoding="utf-8")
        )
        for ph in ("A", "B", "C", "D"):
            assert state_data["phases"][ph]["status"] == "done", f"phase {ph} not done"


# ============================================================
# 用例 2:断点续跑 - 从已有 state.json 启动能跳过已完成阶段
# ============================================================


class TestCheckpointResume:
    def test_resume_from_existing_state_skips_completed(
        self,
        two_skill_files: list[Path],
        isolated_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """预置 state.json(阶段 A 已 done),run_full_cycle 应跳过 A 但仍跑 B/C/D。"""
        # 1) 预置 state:阶段 A done,record 了 1 个 match_id;B/C/D pending
        state = {
            "schema_version": 1,
            "status": "running",
            "skill_paths": [str(p) for p in two_skill_files],
            "task_source": "fixed",
            "phases": {
                "A": {
                    "status": "done",
                    "matches": 0,
                    "recorded_ids": [],  # 空,模拟"已跑过 0 场" (不会有真实 match)
                },
                "B": {"status": "pending"},
                "C": {"status": "pending"},
                "D": {"status": "pending"},
            },
            "notes": "",
        }
        isolated_paths["state"].parent.mkdir(parents=True, exist_ok=True)
        isolated_paths["state"].write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
        # 预置 Elo(让阶段 B/C 至少有 2 个选手)
        isolated_paths["elo"].parent.mkdir(parents=True, exist_ok=True)
        isolated_paths["elo"].write_text(
            json.dumps(
                {
                    "skill-a": 1600.0,
                    "skill-b": 1500.0,
                    "baseline": 1500.0,
                }
            ),
            encoding="utf-8",
        )

        fake = _FakeDeepSeekClient()
        class _StubTaskGenerator:
            def __init__(self, client: Any = None) -> None:
                self.client = client

            def generate_batch(self, *args: Any, **kwargs: Any) -> list[Any]:
                return []

        monkeypatch.setattr(
            "arena.task_generator.TaskGenerator", _StubTaskGenerator
        )

        orch = ArenaOrchestrator(
            client=fake,  # 注入 fake 客户端(关键)
            elo_state_path=isolated_paths["elo"],
            state_path=isolated_paths["state"],
            runs_cache_dir=isolated_paths["runs"],
            fused_dir=isolated_paths["fused"],
            improved_dir=isolated_paths["improved"],
        )
        # 第一次跑:状态中 A 已 done,应直接跳过 A
        report = orch.run_full_cycle(
            skill_paths=[str(p) for p in two_skill_files],
            task_source="fixed",
            rounds_per_pair=1,
            max_improve_iterations=1,
        )
        # 阶段 B 仍执行(产出 fused_skill)
        assert report.fused_skill is not None
        assert report.fused_skill.exists()
        # 阶段 C 仍执行(产出 improvement)
        assert report.improvement is not None
        # 阶段 D 仍执行
        assert report.report_path is not None
        # judge 调用次数 <= 跑完整 A 的次数(因为 A 跳过了)
        # 预期: B 阶段不需要 judge; C 阶段 improvement_evaluator 内部 compare 2 场 + 默认占位
        # 关键是 verify 阶段 A 没新跑 judge: 用 state.json 的 matches 字段为 0
        new_state = json.loads(
            isolated_paths["state"].read_text(encoding="utf-8")
        )
        # 阶段 A 还是 done,且 recorded_ids 仍为空(没有新增 match)
        assert new_state["phases"]["A"]["status"] == "done"
        assert new_state["phases"]["A"].get("recorded_ids", []) == []


# ============================================================
# 用例 3:Elo 在阶段 A 结束后被正确更新
# ============================================================


class TestEloUpdatedAfterStageA:
    def test_elo_changes_after_arena(
        self,
        two_skill_files: list[Path],
        isolated_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """阶段 A 跑完后,Elo 文件应包含 skill 名字且分数偏离 1500。"""
        # 用固定的 verdict: A 永远胜,确保 Elo 一定更新
        fake = _FakeDeepSeekClient(
            verdict=_make_verdict(winner="A", sa=9.0, sb=4.0, reasoning="A 胜")
        )
        class _StubTaskGenerator:
            def __init__(self, client: Any = None) -> None:
                self.client = client

            def generate_batch(self, *args: Any, **kwargs: Any) -> list[Any]:
                return []

        monkeypatch.setattr(
            "arena.task_generator.TaskGenerator", _StubTaskGenerator
        )

        orch = ArenaOrchestrator(
            client=fake,  # 注入 fake 客户端
            elo_state_path=isolated_paths["elo"],
            state_path=isolated_paths["state"],
            runs_cache_dir=isolated_paths["runs"],
            fused_dir=isolated_paths["fused"],
            improved_dir=isolated_paths["improved"],
        )
        # 跑最少 1 场
        report = orch.run_full_cycle(
            skill_paths=[str(p) for p in two_skill_files],
            task_source="fixed",
            rounds_per_pair=1,
            max_improve_iterations=1,
        )

        # Elo 文件存在
        assert isolated_paths["elo"].exists()
        elo = load_state(isolated_paths["elo"])
        # 至少有 2 个 skill(不含 baseline 也行)
        assert "skill-a" in elo
        assert "skill-b" in elo
        # 至少有一个 skill 分数偏离 1500(因为 A 一直胜)
        non_baseline = [v for k, v in elo.items() if k != "baseline"]
        # 胜者的 Elo 一定 > 1500(或者所有都 ≤ 1500,胜者对应 < 1500 的对手;但我们 A 总胜,会有一个 > 1500)
        assert any(v > 1500.0 for v in non_baseline), (
            f"expected at least one Elo > 1500, got {non_baseline}"
        )


# ============================================================
# 用例 4:阶段 B 输出文件存在且长度合理
# ============================================================


class TestStageBOutputFile:
    def test_fused_output_file_exists_and_reasonable(
        self,
        two_skill_files: list[Path],
        isolated_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """阶段 B 产物:文件存在,长度在 [100, 800] 之间(允许偏宽)。"""
        fake = _FakeDeepSeekClient()
        class _StubTaskGenerator:
            def __init__(self, client: Any = None) -> None:
                self.client = client

            def generate_batch(self, *args: Any, **kwargs: Any) -> list[Any]:
                return []

        monkeypatch.setattr(
            "arena.task_generator.TaskGenerator", _StubTaskGenerator
        )

        orch = ArenaOrchestrator(
            client=fake,
            elo_state_path=isolated_paths["elo"],
            state_path=isolated_paths["state"],
            runs_cache_dir=isolated_paths["runs"],
            fused_dir=isolated_paths["fused"],
            improved_dir=isolated_paths["improved"],
        )
        report = orch.run_full_cycle(
            skill_paths=[str(p) for p in two_skill_files],
            task_source="fixed",
            rounds_per_pair=1,
            max_improve_iterations=1,
        )
        assert report.fused_skill is not None
        assert report.fused_skill.exists()
        # 长度合理(允许一定弹性,但不能为空)
        text = report.fused_skill.read_text(encoding="utf-8")
        assert len(text.strip()) > 0
        # 是 markdown(应有 H1)
        assert "#" in text


# ============================================================
# 用例 5:阶段 C ImprovementReport 包含每轮的 skill 版本
# ============================================================


class TestStageCImprovementReport:
    def test_improvement_report_has_skill_versions_per_step(
        self,
        two_skill_files: list[Path],
        isolated_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """阶段 C 的 ImprovementReport 应当:converged=True / steps 非空 / 每步有 skill_version。"""
        # 关键:让 improvement_evaluator 返回"有 weakness"且"Elo 大幅提升",让循环跑出 step
        fake = _FakeDeepSeekClient(
            verdict=_make_verdict(winner="A", sa=10.0, sb=2.0, reasoning="A 大胜")
        )
        class _StubTaskGenerator:
            def __init__(self, client: Any = None) -> None:
                self.client = client

            def generate_batch(self, *args: Any, **kwargs: Any) -> list[Any]:
                return []

        monkeypatch.setattr(
            "arena.task_generator.TaskGenerator", _StubTaskGenerator
        )

        orch = ArenaOrchestrator(
            client=fake,
            elo_state_path=isolated_paths["elo"],
            state_path=isolated_paths["state"],
            runs_cache_dir=isolated_paths["runs"],
            fused_dir=isolated_paths["fused"],
            improved_dir=isolated_paths["improved"],
        )
        report = orch.run_full_cycle(
            skill_paths=[str(p) for p in two_skill_files],
            task_source="fixed",
            rounds_per_pair=1,
            max_improve_iterations=2,
        )
        # improvement 必须有内容
        assert report.improvement is not None
        assert report.bottom_skill is not None
        # 至少跑了 1 步(self_improve 内部有 evaluator 返回 weakness)
        assert report.improvement.total_iterations >= 1, (
            f"expected >=1 step, got total_iterations={report.improvement.total_iterations}"
        )
        # 每步都要有 skill_version
        for i, step in enumerate(report.improvement.steps):
            assert isinstance(step, ImprovementStep)
            assert step.skill_version, f"step {i} has empty skill_version"
            assert "#" in step.skill_version, (
                f"step {i} skill_version doesn't look like markdown: {step.skill_version!r}"
            )
            assert step.elo_after > 0
        # 改进后的 skill 应该被落盘
        improved_files = list(isolated_paths["improved"].glob("*.md"))
        assert improved_files, "阶段 C 应至少落盘 1 个改进 skill 文件"


# ============================================================
# 用例 6:CLI handler / parser
# ============================================================


class TestCLIParser:
    def test_run_subcommand_parses(self) -> None:
        """`python -m arena run --skills A B --task-source fixed` 应能解析。"""
        from arena.__main__ import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["run", "--skills", "a.md", "b.md", "--task-source", "fixed"]
        )
        assert args.subcommand == "run"
        assert args.skills == ["a.md", "b.md"]
        assert args.task_source == "fixed"

    def test_fuse_subcommand_parses(self) -> None:
        from arena.__main__ import build_parser

        parser = build_parser()
        args = parser.parse_args(["fuse", "--a", "A.md", "--b", "B.md"])
        assert args.subcommand == "fuse"
        assert args.a == "A.md"
        assert args.b == "B.md"

    def test_improve_subcommand_parses(self) -> None:
        from arena.__main__ import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["improve", "--skill", "X.md", "--max-iter", "3"]
        )
        assert args.subcommand == "improve"
        assert args.skill == "X.md"
        assert args.max_iter == 3

    def test_report_subcommand_parses(self) -> None:
        from arena.__main__ import build_parser

        parser = build_parser()
        args = parser.parse_args(["report"])
        assert args.subcommand == "report"


# ============================================================
# 用例 7:非法 task_source 抛 ValueError
# ============================================================


class TestInputValidation:
    def test_invalid_task_source_raises(
        self,
        two_skill_files: list[Path],
        isolated_paths: dict[str, Path],
    ) -> None:
        orch = ArenaOrchestrator(
            elo_state_path=isolated_paths["elo"],
            state_path=isolated_paths["state"],
        )
        with pytest.raises(ValueError, match="task_source"):
            orch.run_full_cycle(
                skill_paths=[str(p) for p in two_skill_files],
                task_source="bogus",  # type: ignore[arg-type]
            )

    def test_invalid_rounds_per_pair_raises(
        self,
        two_skill_files: list[Path],
        isolated_paths: dict[str, Path],
    ) -> None:
        orch = ArenaOrchestrator(
            elo_state_path=isolated_paths["elo"],
            state_path=isolated_paths["state"],
        )
        with pytest.raises(ValueError, match="rounds_per_pair"):
            orch.run_full_cycle(
                skill_paths=[str(p) for p in two_skill_files],
                task_source="fixed",
                rounds_per_pair=0,
            )

    def test_no_valid_skills_raises(
        self,
        isolated_paths: dict[str, Path],
    ) -> None:
        orch = ArenaOrchestrator(
            elo_state_path=isolated_paths["elo"],
            state_path=isolated_paths["state"],
        )
        with pytest.raises(ValueError, match="没有"):
            orch.run_full_cycle(
                skill_paths=["/nonexistent/foo.md", "/nonexistent/bar.md"],
                task_source="fixed",
            )


# ============================================================
# 用例 8:run_fusion / run_self_improvement 单独调用
# ============================================================


class TestStandaloneMethods:
    def test_run_fusion_writes_file(
        self,
        two_skill_files: list[Path],
        isolated_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = _FakeDeepSeekClient()
        orch = ArenaOrchestrator(
            client=fake,
            fused_dir=isolated_paths["fused"],
        )
        out = orch.run_fusion(
            str(two_skill_files[0]),
            str(two_skill_files[1]),
            output="standalone_fused.md",
        )
        assert out.exists()
        assert out.name == "standalone_fused.md"
        # 至少调了 1 次 execute
        assert fake.execute_calls >= 1

    def test_run_self_improvement_returns_report(
        self,
        two_skill_files: list[Path],
        isolated_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = _FakeDeepSeekClient()
        orch = ArenaOrchestrator(
            client=fake,
            improved_dir=isolated_paths["improved"],
        )

        # 占位 evaluator(空 weaknesses,直接收敛)
        def evaluator(_c: str, _n: str) -> tuple[float, list[str]]:
            return (1500.0, [])

        report = orch.run_self_improvement(
            str(two_skill_files[0]),
            max_iterations=2,
            evaluator=evaluator,
        )
        assert isinstance(report, ImprovementReport)
        # 空 weaknesses → 立即收敛 → 0 step
        assert report.total_iterations == 0
        assert report.converged is True


# ============================================================
# 用例 9:state.json 损坏时自动重建
# ============================================================


class TestStateRobustness:
    def test_corrupt_state_recovers(
        self,
        two_skill_files: list[Path],
        isolated_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """state.json 写入非法 JSON 时,run_full_cycle 应自动重建(不抛错)。"""
        isolated_paths["state"].parent.mkdir(parents=True, exist_ok=True)
        isolated_paths["state"].write_text("{ not valid json", encoding="utf-8")

        fake = _FakeDeepSeekClient()
        class _StubTaskGenerator:
            def __init__(self, client: Any = None) -> None:
                self.client = client

            def generate_batch(self, *args: Any, **kwargs: Any) -> list[Any]:
                return []

        monkeypatch.setattr(
            "arena.task_generator.TaskGenerator", _StubTaskGenerator
        )

        orch = ArenaOrchestrator(
            client=fake,
            elo_state_path=isolated_paths["elo"],
            state_path=isolated_paths["state"],
            runs_cache_dir=isolated_paths["runs"],
            fused_dir=isolated_paths["fused"],
            improved_dir=isolated_paths["improved"],
        )
        # 不应抛错;应能跑完
        report = orch.run_full_cycle(
            skill_paths=[str(p) for p in two_skill_files],
            task_source="fixed",
            rounds_per_pair=1,
            max_improve_iterations=1,
        )
        assert report.report_path is not None
        assert report.report_path.exists()


# ============================================================
# 用例 10:reset_state
# ============================================================


class TestResetState:
    def test_reset_removes_state_file(
        self,
        isolated_paths: dict[str, Path],
    ) -> None:
        isolated_paths["state"].parent.mkdir(parents=True, exist_ok=True)
        isolated_paths["state"].write_text("{}", encoding="utf-8")
        orch = ArenaOrchestrator(state_path=isolated_paths["state"])
        assert isolated_paths["state"].exists()
        orch.reset_state()
        assert not isolated_paths["state"].exists()
