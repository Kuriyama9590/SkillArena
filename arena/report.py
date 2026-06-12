"""Markdown 对比报告生成器。

输入:每场比赛的 MatchResult + 当前 Elo 状态。
输出:reports/ 下的 Markdown 文件,包含排行榜、胜率、最近明细。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import REPORTS_DIR, ensure_reports_dir
from .elo import load_state
from .judge import Verdict


@dataclass(frozen=True)
class MatchResult:
    """一场比赛的完整记录。

    Attributes:
        match_id: 唯一标识(可由调用方生成,如 "writing-001#001")。
        timestamp: ISO 格式时间戳。
        task_id: 任务 ID(如 writing-001)。
        task_prompt: 任务原文。
        skill_a / skill_b: 双方 skill 名称。
        output_a / output_b: 双方产物文本(便于溯源,可省略)。
        verdict: 评判结果。
    """

    match_id: str
    timestamp: str
    task_id: str
    task_prompt: str
    skill_a: str
    skill_b: str
    verdict: Verdict
    output_a: str = ""
    output_b: str = ""


# -------- 统计辅助 --------

@dataclass
class _SkillStats:
    """单个 skill 在所有比赛中的累计统计。"""

    name: str
    matches: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    score_sum: float = 0.0  # 四维加和,便于算平均

    @property
    def avg_score(self) -> float:
        if self.matches == 0:
            return 0.0
        return self.score_sum / self.matches

    @property
    def win_rate(self) -> float:
        if self.matches == 0:
            return 0.0
        return self.wins / self.matches


def _aggregate_stats(records: Iterable[MatchResult]) -> dict[str, _SkillStats]:
    """聚合每个 skill 的胜负与平均分。"""
    stats: dict[str, _SkillStats] = defaultdict(lambda: _SkillStats(name=""))

    def _ensure(name: str) -> _SkillStats:
        if name not in stats:
            stats[name] = _SkillStats(name=name)
        return stats[name]

    for rec in records:
        s_a = _ensure(rec.skill_a)
        s_b = _ensure(rec.skill_b)

        # 胜/平/负
        if rec.verdict.winner == "A":
            s_a.wins += 1
            s_b.losses += 1
        elif rec.verdict.winner == "B":
            s_b.wins += 1
            s_a.losses += 1
        else:
            s_a.draws += 1
            s_b.draws += 1

        # 双方场次都 +1
        s_a.matches += 1
        s_b.matches += 1

        # 累计维度分数(双方都计)
        s_a.score_sum += rec.verdict.total_score("A")
        s_b.score_sum += rec.verdict.total_score("B")

    return dict(stats)


# -------- 主入口 --------

def generate_report(
    records: list[MatchResult],
    elo_state: dict[str, float] | None = None,
    *,
    output_path: Path | None = None,
    title: str = "Skill 竞技场 · Elo 报告",
) -> Path:
    """生成 Markdown 对比报告并返回写入的文件路径。

    Args:
        records: 全部比赛记录。
        elo_state: 当前 Elo 分数。若 None,会尝试从 reports/elo_state.json 加载。
        output_path: 输出路径,默认 reports/report_YYYYMMDD_HHMMSS.md。
        title: 报告标题。

    Returns:
        实际写入的 Path。
    """
    ensure_reports_dir()
    elo_state = elo_state if elo_state is not None else load_state()

    stats = _aggregate_stats(records)

    # 排序:Elo 优先,然后胜率,再平均分
    def _sort_key(name: str) -> tuple[float, float, float]:
        s = stats.get(name, _SkillStats(name=name))
        return (
            elo_state.get(name, 1500.0),
            s.win_rate,
            s.avg_score,
        )

    sorted_skills = sorted(stats.keys(), key=_sort_key, reverse=True)

    md = _render_markdown(
        records=records,
        elo_state=elo_state,
        stats=stats,
        sorted_skills=sorted_skills,
        title=title,
    )

    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = REPORTS_DIR / f"report_{ts}.md"

    output_path.write_text(md, encoding="utf-8")
    return output_path


def _render_markdown(
    *,
    records: list[MatchResult],
    elo_state: dict[str, float],
    stats: dict[str, _SkillStats],
    sorted_skills: list[str],
    title: str,
) -> str:
    """渲染完整的 Markdown 内容。"""
    lines: list[str] = []
    now = datetime.now().isoformat(timespec="seconds")

    # 顶部
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- 生成时间:`{now}`")
    lines.append(f"- 总场次:**{len(records)}**")
    lines.append(f"- 参与 skill 数:**{len(sorted_skills)}**")
    lines.append("")

    # Elo 排行榜
    lines.append("## Elo 排行榜")
    lines.append("")
    lines.append("| 排名 | Skill | Elo | 胜率 | 平均分 | 场次 |")
    lines.append("|:----:|:------|----:|:----:|:------:|:----:|")
    for i, name in enumerate(sorted_skills, start=1):
        s = stats.get(name, _SkillStats(name=name))
        elo = elo_state.get(name, 1500.0)
        lines.append(
            f"| {i} | `{name}` | {elo:.1f} | {s.win_rate * 100:.1f}% | "
            f"{s.avg_score:.2f} | {s.matches} |"
        )
    lines.append("")

    # 胜/平/负 明细
    lines.append("## 各 skill 战绩明细")
    lines.append("")
    lines.append("| Skill | 胜 | 平 | 负 | 场次 |")
    lines.append("|:------|--:|--:|--:|----:|")
    for name in sorted_skills:
        s = stats.get(name, _SkillStats(name=name))
        lines.append(
            f"| `{name}` | {s.wins} | {s.draws} | {s.losses} | {s.matches} |"
        )
    lines.append("")

    # 最近 10 场
    lines.append("## 最近 10 场比赛")
    lines.append("")
    lines.append("| 时间 | 任务 | A | B | 胜者 | A 分 | B 分 | 理由 |")
    lines.append("|:----:|:----:|:-:|:-:|:----:|----:|----:|:-----|")
    for rec in records[-10:]:
        lines.append(
            f"| `{rec.timestamp}` | `{rec.task_id}` | `{rec.skill_a}` | "
            f"`{rec.skill_b}` | **{rec.verdict.winner}** | "
            f"{rec.verdict.total_score('A'):.1f} | "
            f"{rec.verdict.total_score('B'):.1f} | "
            f"{rec.verdict.reasoning} |"
        )
    lines.append("")

    return "\n".join(lines)


__all__ = ["MatchResult", "generate_report"]