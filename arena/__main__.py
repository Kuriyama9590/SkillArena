"""CLI 入口:`python -m arena <subcommand>`。

子命令:
  run     - 跑一次完整 run_full_cycle(阶段 A→B→C→D)。
  fuse    - 单独融合两个 skill(阶段 B)。
  improve - 单独跑自改进循环(阶段 C)。
  report  - 重新生成 Markdown 报告(阶段 D)。
  reset   - 删除 orchestrator state(下次 run 会重跑)。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from .config import REPORTS_DIR
from .orchestrator import ArenaOrchestrator

logger = logging.getLogger(__name__)


# ============================================================
# 子命令 handler
# ============================================================


def _cmd_run(args: argparse.Namespace) -> int:
    orch = ArenaOrchestrator()
    report = orch.run_full_cycle(
        skill_paths=args.skills,
        task_source=args.task_source,
        auto_categories=args.auto_categories,
        auto_per_category=args.auto_per_category,
        rounds_per_pair=args.rounds_per_pair,
        fused_output_name=args.fused_output,
        max_improve_iterations=args.max_improve_iter,
        run_fusion=not args.skip_fusion,
        run_improvement=not args.skip_improvement,
        report_title=args.title,
    )
    print(f"[run] Elo 选手数: {len(report.elo_state)}")
    print(f"[run] 比赛数: {len(report.matches)}")
    if report.fused_skill:
        print(f"[run] 融合产物: {report.fused_skill}")
    if report.improvement:
        print(
            f"[run] 自改进: skill={report.bottom_skill}, "
            f"iters={report.improvement.total_iterations}, "
            f"converged={report.improvement.converged}"
        )
    if report.report_path:
        print(f"[run] 报告: {report.report_path}")
    return 0


def _cmd_fuse(args: argparse.Namespace) -> int:
    orch = ArenaOrchestrator()
    out = orch.run_fusion(
        skill_a=args.a,
        skill_b=args.b,
        output=args.output,
        task_context=args.task_context,
        judge_feedback=args.judge_feedback,
    )
    print(f"[fuse] 写入 {out}")
    return 0


def _cmd_improve(args: argparse.Namespace) -> int:
    orch = ArenaOrchestrator()

    # 注入 evaluator:跑固定任务子集 vs baseline
    def evaluator(skill_content: str, skill_name: str) -> tuple[float, list[str]]:
        # 占位:CLI 场景使用默认占位(返回 1500, []);生产应调 orchestrator 内部逻辑
        return (1500.0, [])

    report = orch.run_self_improvement(
        skill_path=args.skill,
        max_iterations=args.max_iter,
        target_elo_delta=args.target_elo_delta,
        evaluator=evaluator if args.use_evaluator else None,
    )
    print(
        f"[improve] skill={report.skill_name} "
        f"iters={report.total_iterations} converged={report.converged} "
        f"final_elo={report.final_elo:.1f}"
    )
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    orch = ArenaOrchestrator()
    path = orch.regenerate_report(title=args.title)
    print(f"[report] 写入 {path}")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    orch = ArenaOrchestrator()
    orch.reset_state()
    print("[reset] orchestrator state 已清空")
    return 0


# ============================================================
# argparse 构建
# ============================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m arena",
        description="Skill 竞技场 CLI",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # ---- run ----
    p_run = sub.add_parser("run", help="跑一次完整竞技场(阶段 A→B→C→D)")
    p_run.add_argument(
        "--skills",
        nargs="+",
        required=True,
        help="参与 skill 路径(空格分隔多个 .md 路径)",
    )
    p_run.add_argument(
        "--task-source",
        choices=["fixed", "auto", "hybrid"],
        default="fixed",
        help="任务来源(默认 fixed)",
    )
    p_run.add_argument(
        "--auto-categories",
        nargs="*",
        default=None,
        help="auto/hybrid 模式下要生成的类目(默认 writing/coding/analysis)",
    )
    p_run.add_argument(
        "--auto-per-category",
        type=int,
        default=3,
        help="每个类目生成几个 auto 任务(默认 3)",
    )
    p_run.add_argument(
        "--rounds-per-pair",
        type=int,
        default=2,
        help="每个 (task, skill_a, skill_b) 跑几轮 Elo(默认 2)",
    )
    p_run.add_argument(
        "--fused-output",
        default=None,
        help="阶段 B 融合产物的文件名(默认 <a>__<b>_fused.md)",
    )
    p_run.add_argument(
        "--max-improve-iter",
        type=int,
        default=2,
        help="阶段 C 自改进最大迭代(默认 2)",
    )
    p_run.add_argument(
        "--skip-fusion",
        action="store_true",
        help="跳过阶段 B",
    )
    p_run.add_argument(
        "--skip-improvement",
        action="store_true",
        help="跳过阶段 C",
    )
    p_run.add_argument(
        "--title",
        default="Skill 竞技场 · 全量循环报告",
        help="报告标题",
    )
    p_run.set_defaults(handler=_cmd_run)

    # ---- fuse ----
    p_fuse = sub.add_parser("fuse", help="单独融合两个 skill")
    p_fuse.add_argument("--a", required=True, help="skill A 路径")
    p_fuse.add_argument("--b", required=True, help="skill B 路径")
    p_fuse.add_argument(
        "--output",
        default=None,
        help="融合产物的文件名(默认 <a>__<b>_fused.md)",
    )
    p_fuse.add_argument(
        "--task-context",
        default="通用写作任务",
        help="任务上下文",
    )
    p_fuse.add_argument(
        "--judge-feedback",
        default="",
        help="评判反馈(可空)",
    )
    p_fuse.set_defaults(handler=_cmd_fuse)

    # ---- improve ----
    p_impr = sub.add_parser("improve", help="单独跑自改进循环")
    p_impr.add_argument("--skill", required=True, help="skill 路径")
    p_impr.add_argument(
        "--max-iter",
        type=int,
        default=3,
        help="最大迭代次数(默认 3)",
    )
    p_impr.add_argument(
        "--target-elo-delta",
        type=float,
        default=20.0,
        help="单轮 Elo 提升目标(默认 20.0)",
    )
    p_impr.add_argument(
        "--use-evaluator",
        action="store_true",
        help="注入内置 evaluator(否则用占位 evaluator)",
    )
    p_impr.set_defaults(handler=_cmd_improve)

    # ---- report ----
    p_rep = sub.add_parser("report", help="重新生成 Markdown 报告")
    p_rep.add_argument(
        "--title",
        default="Skill 竞技场 · Elo 报告(重新生成)",
        help="报告标题",
    )
    p_rep.set_defaults(handler=_cmd_report)

    # ---- reset ----
    p_reset = sub.add_parser("reset", help="清空 orchestrator state")
    p_reset.set_defaults(handler=_cmd_reset)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
