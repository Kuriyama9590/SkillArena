"""import_user_skills.py — 把 import_skills/ 下的用户 skill 自动入库到 skills/ 下。

行为约定:
1. 遍历 ``--import-dir`` 下所有 ``.md`` / ``.txt`` / ``.yaml`` / ``.yml`` 文件(递归)。
2. 文件 → 目标 markdown:
     - ``.md``: 原样复制,只加 4 行注释头。
     - ``.yaml`` / ``.yml``: 尝试 ``yaml.safe_load``;成功则用 ``yaml.safe_dump`` 重新渲染;
       失败则降级为纯文本(用 ``yaml.YAMLError`` 捕获)。
     - ``.txt``: 视为纯文本,用 ``text`` 代码块包起来。
3. 输出文件名前缀统一为 ``imported-``,后缀固定为 ``.md``:
     ``skills/imported-<原文件名>.md``
4. 幂等:如果目标文件已存在,解析头部拿到旧 SHA256 前缀;一致则跳过,不一致则覆盖并 warn。
5. 重复跑不报错;空目录 / 不存在的目录都正常处理(打印"无待导入文件")。
6. exit code: 0 = 成功(包含"全部跳过"和"空目录"),1 = 任意文件入库过程中出错。

Header 格式(写入每个目标文件最前面):
    # Source: imported from import_skills/<原文件名>
    # Imported: 2026-06-12
    # Bytes: <原始字节数>
    # SHA256: <前 12 位>

用法:
    python scripts/import_user_skills.py
    python scripts/import_user_skills.py --import-dir ./import_skills --output-dir ./skills
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import yaml

# 支持的扩展名(大小写不敏感比较时用 .lower())
SUPPORTED_SUFFIXES: tuple[str, ...] = (".md", ".markdown", ".txt", ".yaml", ".yml")

# Header 里 SHA256 行的正则前缀,用于幂等检测
SHA_HEADER_PREFIX = "# SHA256: "
SOURCE_HEADER_PREFIX = "# Source: imported from "
IMPORT_DATE_HEADER_PREFIX = "# Imported: "
BYTES_HEADER_PREFIX = "# Bytes: "

HEADER_LINES = 4  # 4 行注释头


@dataclass(frozen=True)
class ImportResult:
    """单个文件的处理结果。"""

    src: Path
    dst: Optional[Path]
    action: str  # "imported" | "skipped" | "overwritten" | "error"
    message: str = ""


def _iter_candidates(import_dir: Path) -> Iterable[Path]:
    """递归 yield ``import_dir`` 下所有受支持后缀的文件,按路径排序保证可重复。

    ``import_dir`` 不存在时静默返回空迭代器(由调用方判定"无待导入文件")。
    """
    if not import_dir.exists() or not import_dir.is_dir():
        return
    yield from sorted(
        p
        for p in import_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def _sha256_prefix(data: bytes, length: int = 12) -> str:
    """计算 ``data`` 的 SHA256 并返回前 ``length`` 位 hex。"""
    return hashlib.sha256(data).hexdigest()[:length]


def _read_existing_sha(dst: Path) -> Optional[str]:
    """从已存在的目标文件头部解析 SHA256;不存在或解析失败返回 None。"""
    if not dst.exists():
        return None
    try:
        with dst.open("r", encoding="utf-8") as fh:
            for _ in range(HEADER_LINES):
                line = fh.readline()
                if not line:
                    return None
                if line.startswith(SHA_HEADER_PREFIX):
                    return line[len(SHA_HEADER_PREFIX) :].strip()
    except OSError:
        return None
    return None


def _build_header(src: Path, raw_bytes: bytes, sha_prefix: str) -> str:
    """生成 4 行注释头。"""
    rel = src.name  # 任务要求用"原文件名",不强制带相对路径
    return (
        f"# Source: imported from import_skills/{rel}\n"
        f"# Imported: {date.today().isoformat()}\n"
        f"# Bytes: {len(raw_bytes)}\n"
        f"# SHA256: {sha_prefix}\n"
    )


def _wrap_as_markdown(src_path: Path, raw_bytes: bytes) -> str:
    """把非 .md 源文件内容包成 markdown 字符串。"""
    text = raw_bytes.decode("utf-8", errors="replace")
    suffix = src_path.suffix.lower()

    if suffix in (".yaml", ".yml"):
        # 尝试当 yaml 解析;失败就降级为纯文本
        try:
            parsed = yaml.safe_load(text)
            if parsed is None:
                # 空文件 / 仅注释,直接原文输出
                rendered = text
            elif isinstance(parsed, (str, int, float, bool)):
                # 标量 yaml 仍保留为 code block 更稳
                rendered = text
            else:
                rendered = yaml.safe_dump(
                    parsed, allow_unicode=True, sort_keys=False, default_flow_style=False
                )
            return f"```yaml\n{rendered.rstrip()}\n```\n"
        except yaml.YAMLError:
            return f"```\n{text.rstrip()}\n```\n"

    # .txt / .markdown / 其它都按纯文本处理
    return f"```text\n{text.rstrip()}\n```\n"


def _build_body(src_path: Path, raw_bytes: bytes) -> str:
    """根据源文件后缀,决定直接复制还是包成 markdown 块。"""
    suffix = src_path.suffix.lower()
    if suffix in (".md", ".markdown"):
        # 原 .md 也要 trim 末尾空行,避免和 header 之间出现多余空行
        return raw_bytes.decode("utf-8", errors="replace").rstrip() + "\n"
    return _wrap_as_markdown(src_path, raw_bytes)


def _import_one(src: Path, output_dir: Path) -> ImportResult:
    """处理单个源文件;返回 ImportResult。捕获所有异常并以 action='error' 上报。"""
    try:
        raw_bytes = src.read_bytes()
    except OSError as exc:
        return ImportResult(src=src, dst=None, action="error", message=f"read failed: {exc}")

    sha_prefix = _sha256_prefix(raw_bytes)
    header = _build_header(src, raw_bytes, sha_prefix)
    body = _build_body(src, raw_bytes)
    payload = header + body

    dst = output_dir / f"imported-{src.name}"
    if not dst.suffix:
        dst = dst.with_suffix(".md")
    elif dst.suffix.lower() != ".md":
        # 源文件是 foo.yaml → 目标是 imported-foo.yaml.md,避免后缀混乱
        dst = dst.with_suffix(dst.suffix + ".md")

    # 幂等检测
    existing_sha = _read_existing_sha(dst)
    if existing_sha == sha_prefix:
        return ImportResult(src=src, dst=dst, action="skipped", message="sha256 match")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        # utf-8 + 换行符统一,便于 git diff
        dst.write_text(payload, encoding="utf-8", newline="\n")
    except OSError as exc:
        return ImportResult(src=src, dst=dst, action="error", message=f"write failed: {exc}")

    if existing_sha is None:
        return ImportResult(src=src, dst=dst, action="imported")
    return ImportResult(
        src=src,
        dst=dst,
        action="overwritten",
        message=f"sha256 changed: {existing_sha} -> {sha_prefix}",
    )


def run(import_dir: Path, output_dir: Path) -> tuple[list[ImportResult], int]:
    """主流程;返回 (results, error_count)。"""
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[ImportResult] = []
    for src in _iter_candidates(import_dir):
        results.append(_import_one(src, output_dir))

    error_count = sum(1 for r in results if r.action == "error")
    return results, error_count


def _format_summary(results: list[ImportResult]) -> str:
    """汇总统计行(纯文本,适合直接 print)。"""
    n_total = len(results)
    n_imported = sum(1 for r in results if r.action == "imported")
    n_overwritten = sum(1 for r in results if r.action == "overwritten")
    n_skipped = sum(1 for r in results if r.action == "skipped")
    n_error = sum(1 for r in results if r.action == "error")
    return (
        f"扫描到 {n_total} 个文件,"
        f"成功导入 {n_imported} 个,覆盖 {n_overwritten} 个,"
        f"跳过 {n_skipped} 个(已存在且一致),"
        f"错误 {n_error} 个"
    )


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 import_skills/ 下的用户 skill 文件入库到 skills/ 下。",
    )
    parser.add_argument(
        "--import-dir",
        type=Path,
        default=Path("import_skills"),
        help="待扫描的源目录(递归)。默认 import_skills/",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("skills"),
        help="目标目录。默认 skills/",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="只打印汇总行,跳过逐文件日志。",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    import_dir: Path = args.import_dir
    output_dir: Path = args.output_dir

    # 路径转绝对值,避免相对路径在 cwd 变化时表现奇怪
    if not import_dir.is_absolute():
        import_dir = import_dir.resolve()
    if not output_dir.is_absolute():
        output_dir = output_dir.resolve()

    if not import_dir.exists():
        print(f"[import-user-skills] 源目录 {import_dir} 不存在;无待导入文件。")
        return 0
    if not import_dir.is_dir():
        print(f"[import-user-skills] {import_dir} 不是目录,无法扫描。", file=sys.stderr)
        return 1

    results, error_count = run(import_dir, output_dir)

    if not results:
        print("[import-user-skills] 无待导入文件。")
        return 0

    if not args.quiet:
        for r in results:
            tag = f"[{r.action}]"
            target = r.dst.name if r.dst is not None else "-"
            extra = f" ({r.message})" if r.message else ""
            print(f"{tag} {r.src.name} -> {target}{extra}")

    print(f"[import-user-skills] {_format_summary(results)}")
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
