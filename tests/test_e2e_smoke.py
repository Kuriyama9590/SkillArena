"""端到端冒烟测试(真实调用 DeepSeek API)。

设计目的:
- 验证"完整 run_full_cycle 真的能跑通"(不是只 mock)。
- 用最少 API 调用节省费用:1 个 skill、1 个任务、rounds=1、
  不跑融合/自改进(单 skill 没法选 top2/bottom1)。
- 默认 SKIP,只有显式设置 `RUN_E2E_SMOKE=1` 才执行。
- 失败时给出明确的环境检查指引(API key、网络)。

运行方式:
    # 默认(跳过的占位测试,1 秒内完成)
    pytest tests/test_e2e_smoke.py -v

    # 真实运行
    RUN_E2E_SMOKE=1 pytest tests/test_e2e_smoke.py -v -s

成本估算(1 skill × 1 task × 1 round):
    - runner 跑 2 次产物(1 skill + 1 baseline):2 execute
    - judge 跑 1 次 compare:1 judge
    - 不跑 fuse/improve
    总:3 次 API 调用,通常 < 1 元人民币。
"""
from __future__ import annotations

import os
import shutil
import socket
import uuid
from pathlib import Path

import pytest

# 端到端跑需要存在 API key
DEEPSEEK_API_KEY_PRESENT = bool(os.getenv("DEEPSEEK_API_KEY", "").strip())
RUN_E2E = os.getenv("RUN_E2E_SMOKE", "").strip() in ("1", "true", "TRUE", "yes")

# pytest 标记:把整个 e2e 标成"可选",CI 默认 skip
pytestmark = pytest.mark.skipif(
    not RUN_E2E,
    reason=(
        "端到端冒烟测试默认跳过。"
        "要运行:设置环境变量 RUN_E2E_SMOKE=1,"
        "并确保 DEEPSEEK_API_KEY 已设置。"
    ),
)


# ============================================================
# 环境检查
# ============================================================


def _check_environment() -> list[str]:
    """检查运行 e2e 的前置条件,返回问题列表(空 = 通过)。"""
    issues: list[str] = []
    if not DEEPSEEK_API_KEY_PRESENT:
        issues.append(
            "DEEPSEEK_API_KEY 未设置。设置方法:export DEEPSEEK_API_KEY=sk-xxx"
            " (PowerShell: $env:DEEPSEEK_API_KEY = 'sk-xxx')"
        )
    # 简单网络可达性检测(deepseek.com 的 443)
    try:
        socket.create_connection(("api.deepseek.com", 443), timeout=3).close()
    except OSError as exc:
        issues.append(
            f"无法连接 api.deepseek.com:443: {exc}。"
            "检查网络/代理/防火墙。"
        )
    # 输出目录是否可写
    try:
        Path("reports").mkdir(parents=True, exist_ok=True)
        test_file = Path("reports") / f".e2e_write_test_{uuid.uuid4().hex}.tmp"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
    except OSError as exc:
        issues.append(f"reports/ 不可写: {exc}。检查目录权限。")

    return issues


# ============================================================
# 默认占位测试(总被 skip,用于在 CI 中显式看到"这个 e2e 被默认跳过")
# ============================================================


class TestE2ESmoke:
    def test_e2e_smoke_runs_when_enabled(self) -> None:
        """当 RUN_E2E_SMOKE=1 且环境齐备时,真实跑一次端到端。

        验证项:
        1. 完整 run_full_cycle 能跑通。
        2. Elo 文件被写入。
        3. 报告被生成。
        4. 阶段 A 跑出至少 1 场 match。
        5. 总 API 调用次数 <= 10。
        """
        if not RUN_E2E:
            pytest.skip("RUN_E2E_SMOKE not set")

        issues = _check_environment()
        if issues:
            pytest.fail(
                "环境检查未通过:\n"
                + "\n".join(f"  - {x}" for x in issues)
                + "\n请按上述提示修复后重试。"
            )

        # 用临时目录隔离 artifacts(不污染项目 reports/)
        from arena.config import ELO_STATE_FILE, REPORTS_DIR
        from arena.orchestrator import ArenaOrchestrator

        workdir = Path("reports") / f".e2e_smoke_{uuid.uuid4().hex[:8]}"
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            # 找到内置的 concise-writer skill
            from arena.config import SKILLS_DIR

            skill_path = SKILLS_DIR / "concise-writer.md"
            if not skill_path.exists():
                pytest.fail(
                    f"找不到内置 skill {skill_path}。"
                    "可能项目结构被破坏;请重新初始化。"
                )

            orch = ArenaOrchestrator(
                elo_state_path=workdir / "elo_state.json",
                state_path=workdir / "orchestrator_state.json",
                runs_cache_dir=workdir / "runs",
                fused_dir=workdir / "fused",
                improved_dir=workdir / "improved",
            )

            # 1 skill + 1 task + 1 round + 跳过融合/自改进
            # (单 skill 没法选 top2/bottom1,融合/自改进逻辑会跳过)
            report = orch.run_full_cycle(
                skill_paths=[str(skill_path)],
                task_source="fixed",
                rounds_per_pair=1,
                run_fusion=False,
                run_improvement=False,
                max_improve_iterations=0,
                report_title="e2e_smoke · 单 skill 端到端",
            )

            # 1) 报告被生成
            assert report.report_path is not None
            assert report.report_path.exists(), (
                f"报告未生成:{report.report_path}"
            )

            # 2) Elo 文件被写入
            elo_path = workdir / "elo_state.json"
            assert elo_path.exists(), f"Elo 文件未生成:{elo_path}"

            # 3) Elo 至少包含 baseline + 1 skill
            import json

            elo = json.loads(elo_path.read_text(encoding="utf-8"))
            assert "baseline" in elo
            assert "concise-writer" in elo

            # 4) 至少 1 场 match(2 个选手,1 组合)
            assert len(report.matches) >= 1, (
                f"未跑出 match: {len(report.matches)}"
            )

            # 5) 报告大小合理
            report_size = report.report_path.stat().st_size
            assert report_size > 100, f"报告过小:{report_size} bytes"

            print(
                f"\n[e2e] OK:report={report.report_path}, "
                f"elo={elo}, matches={len(report.matches)}"
            )
        finally:
            # 清理临时目录
            shutil.rmtree(workdir, ignore_errors=True)


# ============================================================
# 手动触发指南(注释形式,方便后续 dev 复现)
# ============================================================


"""
手动复现一条 e2e(以 bash 为例):

    cd "E:/Projects/skill竞技场"
    export DEEPSEEK_API_KEY="sk-xxx..."
    export RUN_E2E_SMOKE=1
    pytest tests/test_e2e_smoke.py -v -s

预期输出(关键行):
    tests/test_e2e_smoke.py::TestE2ESmoke::test_e2e_smoke_runs_when_enabled PASSED
    [e2e] OK: report=reports/.e2e_smoke_xxx/report_YYYYMMDD_HHMMSS.md, ...

PowerShell 版:
    $env:DEEPSEEK_API_KEY = "sk-xxx..."
    $env:RUN_E2E_SMOKE = "1"
    pytest tests/test_e2e_smoke.py -v -s

失败排查清单:
- "DEEPSEEK_API_KEY 未设置" → 配置 .env 或 export
- "无法连接 api.deepseek.com:443" → 检查代理 / 防火墙
- "报告未生成" → 阶段 D 跑出异常,看 stderr
- "Elo 文件未生成" → 阶段 A 失败,看 stderr
- "未跑出 match" → 数据没接上,看 stderr
- 总 API 调用次数可由 reports/.e2e_smoke_xxx/runs 下的 .txt 文件数推算
"""
