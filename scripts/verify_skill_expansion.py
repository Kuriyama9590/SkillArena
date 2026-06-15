"""verify_skill_expansion.py — track4 集成校验脚本。

对 skills/ 下所有 skill 文件做统一校验:
  1. 文件头必须含 ``# Source:`` 注释(seed skill 例外:它们早于该约定,且
     skill-expansion-spec 明确"不修改现有 3 个 seed skill",按 seed 来源登记)。
  2. 正文字符数(跳过以 ``#`` 开头的行后,统计非空白字符)落在区间内:
     - 默认 [100, 400]
     - ``gen-meta-prompt.md`` 放宽到 [100, 600](track5 spec 明确放宽)
  3. 含至少一个指令关键词(中英):写 / 分析 / 生成 / 输出 / review / think /
     step / 步骤 / 原则 / 必须 / 禁止 / 不要 / 先 / 用。
  4. 不包含疑似 API key 字符串(``sk-`` 后跟 20+ 个 base62 字符)。

输出:每个文件的校验表 + 按「类别 / 来源类型」的统计 + 失败明细。
exit code: 0 = 全部通过(含 seed 例外);1 = 存在失败项。

用法:
    python scripts/verify_skill_expansion.py
    python scripts/verify_skill_expansion.py --skills-dir skills --quiet
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# ---- 常量 ------------------------------------------------------------------

SEED_SKILLS: tuple[str, ...] = ("concise-writer", "detailed-writer", "structured-writer")

# 长度放宽名单(文件名 stem -> 上限)
LENGTH_OVERRIDES: dict[str, int] = {"gen-meta-prompt": 600}

MIN_LENGTH = 100
DEFAULT_MAX_LENGTH = 400

# 指令动词/结构标记(中英)。spec 给的是"等"非穷举,这里补齐常见祈使与结构词,
# 目的是排除"纯说明性/许可证/变更日志"这类非 skill 文本,而非逐字匹配。
INSTRUCTION_KEYWORDS: tuple[str, ...] = (
    "写", "分析", "生成", "输出", "review", "think", "step",
    "步骤", "原则", "必须", "禁止", "不要", "先", "用",
    "避免", "遵循", "突出", "给出", "说明", "列出", "检查", "确保",
    "提供", "采用", "描述", "总结", "评估", "规则", "模板", "角色",
)

# 疑似 API key: sk- 后跟 20+ 个 base62 / _ / -
API_KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]{20,}")

CATEGORY_RE = re.compile(r"^#\s*Category:\s*(.+?)\s*$", re.MULTILINE)
SOURCE_RE = re.compile(r"^#\s*Source:\s*(.+?)\s*$", re.MULTILINE)


# ---- 数据结构 --------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    source_type: str  # seed | generated | collected | imported | unknown
    has_source: bool
    body_length: int
    length_ok: bool
    has_keyword: bool
    no_api_key: bool
    category: str
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


# ---- 解析 ------------------------------------------------------------------

def _classify(name: str) -> str:
    if name in SEED_SKILLS:
        return "seed"
    if name.startswith("gen-"):
        return "generated"
    if name.startswith("collected-"):
        return "collected"
    if name.startswith("imported-"):
        return "imported"
    return "unknown"


def _body_length(text: str) -> int:
    """跳过以 ``#`` 开头的行后,统计非空白字符数(项目 Method A 口径)。"""
    body_chars = [
        ch for line in text.splitlines()
        if not line.lstrip().startswith("#")
        for ch in line if not ch.isspace()
    ]
    return len(body_chars)


def check_file(path: Path) -> CheckResult:
    name = path.stem
    source_type = _classify(name)
    text = path.read_text(encoding="utf-8")

    src_match = SOURCE_RE.search(text)
    has_source = src_match is not None
    cat_match = CATEGORY_RE.search(text)
    category = cat_match.group(1).strip() if cat_match else ("writing" if source_type == "seed" else "?")
    body_len = _body_length(text)
    max_len = LENGTH_OVERRIDES.get(name, DEFAULT_MAX_LENGTH)
    length_ok = MIN_LENGTH <= body_len <= max_len
    has_keyword = any(kw in text.lower() for kw in INSTRUCTION_KEYWORDS)
    api_key_hit = API_KEY_RE.search(text)
    no_api_key = api_key_hit is None

    failures: list[str] = []
    # seed skill 按设计无 Source 头,不算失败
    if not has_source and source_type != "seed":
        failures.append("缺 # Source: 文件头注释")
    if not length_ok:
        failures.append(f"正文字符数 {body_len} 不在 [{MIN_LENGTH}, {max_len}]")
    if not has_keyword:
        failures.append("未命中任何指令关键词")
    if not no_api_key:
        failures.append(f"疑似硬编码 API key: {api_key_hit.group(0)[:18]}...")

    return CheckResult(
        name=name,
        source_type=source_type,
        has_source=has_source,
        body_length=body_len,
        length_ok=length_ok,
        has_keyword=has_keyword,
        no_api_key=no_api_key,
        category=category,
        failures=failures,
    )


# ---- 主流程 ----------------------------------------------------------------

def run(skills_dir: Path) -> tuple[list[CheckResult], int]:
    files = sorted(
        p for p in skills_dir.glob("*.md")
        # skills/ 下夹带的 deliverable 文档不是 skill,跳过
        if not p.name.startswith("deliverable-")
    )
    results = [check_file(p) for p in files]
    n_fail = sum(1 for r in results if not r.passed)
    return results, n_fail


def _print_report(results: list[CheckResult], quiet: bool) -> None:
    n_total = len(results)
    n_pass = sum(1 for r in results if r.passed)
    n_fail = n_total - n_pass

    if not quiet:
        print(f"{'文件':<34}{'来源':<11}{'类别':<10}{'字数':>5}  Source  关键词  无key  结果")
        print("-" * 92)
        for r in results:
            print(
                f"{r.name:<34}{r.source_type:<11}{r.category:<10}{r.body_length:>5}  "
                f"{'✓' if r.has_source else '·':<6}"
                f"{'✓' if r.has_keyword else '✗':<7}"
                f"{'✓' if r.no_api_key else '✗':<6}"
                f"{'PASS' if r.passed else 'FAIL'}"
            )
        print("-" * 92)

    print(f"校验汇总: {n_pass}/{n_total} 通过, {n_fail} 失败")

    # 分类统计
    cat_counter = Counter(r.category for r in results)
    print("按类别: " + ", ".join(f"{k}={v}" for k, v in sorted(cat_counter.items())))
    src_counter = Counter(r.source_type for r in results)
    print("按来源: " + ", ".join(f"{k}={v}" for k, v in sorted(src_counter.items())))

    if n_fail:
        print("\n失败明细:")
        for r in results:
            if not r.passed:
                for msg in r.failures:
                    print(f"  ✗ {r.name}: {msg}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="track4: skills/ 统一校验。")
    p.add_argument("--skills-dir", type=Path, default=Path("skills"))
    p.add_argument("--quiet", action="store_true", help="只打印汇总 + 失败明细。")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    results, n_fail = run(args.skills_dir)
    _print_report(results, args.quiet)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
