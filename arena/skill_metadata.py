"""Skill 领域标签解析与推断。

从 .md 文件读取 YAML front matter 中的 `domains` 字段,
若无 front matter 则从文件名和内容自动推断。
无法确定领域时抛出 ValueError——不允许静默回退到 general。

领域标签与 Task.category 对齐:
- writing  → 参与 writing 类 task
- coding   → 参与 coding 类 task
- analysis → 参与 analysis 类 task
- general  → 参与所有 task（必须显式声明）
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

VALID_DOMAINS: tuple[str, ...] = ("writing", "coding", "analysis", "general")
TASK_DOMAINS: tuple[str, ...] = ("writing", "coding", "analysis")


@dataclass(frozen=True)
class SkillEntry:
    """带领域标签的 skill 条目。"""

    name: str
    content: str
    domains: tuple[str, ...]

    def participates_in(self, domain: str) -> bool:
        """判断该 skill 是否参与指定领域的竞技。"""
        if "general" in self.domains:
            return True
        return domain in self.domains


_YAML_FM_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL
)

_FILENAME_KEYWORDS: dict[str, list[str]] = {
    "writing": [
        "writer", "writing", "narrative", "persuasive",
        "email", "wording", "doc", "invitation", "cofounder",
    ],
    "coding": [
        "sql", "code", "reviewer", "shadcn", "android",
        "security", "component",
    ],
    "analysis": [
        "analysis", "market", "scoring", "advocate",
        "critic", "thought", "step-back", "analyzer",
    ],
}

_CONTENT_KEYWORDS: dict[str, list[str]] = {
    "writing": [
        "写作", "文案", "邮件", "叙事", "写作风格",
        "段落", "文章", "文章结构",
    ],
    "coding": [
        "代码", "SQL", "查询", "组件", "React",
        "Android", "安全", "接口",
    ],
    "analysis": [
        "分析", "评估", "推理", "分步", "核查",
        "反方", "自检",
    ],
}


def parse_skill_domains(skill_path: Path) -> list[str]:
    """从 .md 文件解析领域标签。

    优先级:
    1. YAML front matter 中的 `domains` 字段
    2. 从文件名推断
    3. 从内容推断
    4. 均未命中则抛出 ValueError（不允许静默回退到 general）
    """
    content = skill_path.read_text(encoding="utf-8")

    domains = _parse_front_matter(content)
    if domains:
        return _validate_domains(domains)

    domains = _infer_from_filename(skill_path.stem)
    if domains:
        return _validate_domains(domains)

    domains = _infer_from_content(content)
    if domains:
        return _validate_domains(domains)

    raise ValueError(
        f"skill {skill_path.name!r} 未声明 domains 且无法自动推断。"
        f"请在 YAML front matter 中显式声明 domains 字段，"
        f"例如: domains: [writing] 或 domains: [general]"
    )


def _parse_front_matter(content: str) -> list[str] | None:
    """尝试从 YAML front matter 读取 domains 字段。"""
    m = _YAML_FM_RE.match(content)
    if not m:
        return None
    fm_text = m.group(1)
    for line in fm_text.splitlines():
        line = line.strip()
        if line.lower().startswith("domains:"):
            rest = line[len("domains:"):].strip()
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1]
                items = [x.strip().strip("'\"") for x in inner.split(",")]
                return [x for x in items if x]
            return [rest.strip().strip("'\"")]
    return None


def _infer_from_filename(name: str) -> list[str] | None:
    """从文件名推断领域。"""
    name_lower = name.lower()
    matched: set[str] = set()
    for domain, keywords in _FILENAME_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                matched.add(domain)
                break
    if matched:
        return sorted(matched)
    return None


def _infer_from_content(content: str) -> list[str] | None:
    """从内容推断领域(只看前 2000 字符)。"""
    head = content[:2000].lower()
    matched: set[str] = set()
    for domain, keywords in _CONTENT_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in head:
                matched.add(domain)
                break
    if matched:
        return sorted(matched)
    return None


def _validate_domains(domains: list[str]) -> list[str]:
    """校验并过滤无效领域标签。"""
    valid = [d for d in domains if d in VALID_DOMAINS]
    if not valid:
        raise ValueError(
            f"domains 声明 {domains!r} 中无有效标签。"
            f"允许的值: {VALID_DOMAINS}"
        )
    if "general" in valid and len(valid) > 1:
        valid = ["general"]
    return valid


def load_skill_entry(skill_path: Path) -> SkillEntry:
    """加载 skill 文件为 SkillEntry。"""
    content = skill_path.read_text(encoding="utf-8")
    domains = parse_skill_domains(skill_path)
    return SkillEntry(
        name=skill_path.stem,
        content=content,
        domains=tuple(domains),
    )


__all__ = [
    "VALID_DOMAINS",
    "TASK_DOMAINS",
    "SkillEntry",
    "parse_skill_domains",
    "load_skill_entry",
]
