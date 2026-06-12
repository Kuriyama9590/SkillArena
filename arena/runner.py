"""Skill 加载 + 执行。

两个核心职责:
- load_skill:从磁盘读取 skill 文件(.md)的完整内容。
- run_with_skill:把 skill 作为 system message 注入,执行任务并返回产物。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import SKILLS_DIR
from .deepseek_client import CompletionResult, DeepSeekClient


@dataclass(frozen=True)
class RunOutput:
    """一次执行任务的结构化输出。

    Attributes:
        skill_name: 使用的 skill 名称(裸 prompt 时为 None)。
        task: 原始任务 prompt。
        content: 模型产物的文本。
        tokens: token 统计(在 CompletionResult 中)。
        model: 实际使用的模型名。
    """

    skill_name: str | None
    task: str
    content: str
    tokens: int
    model: str


def load_skill(skill_path: str | Path) -> str:
    """读取 skill 文件的完整内容。

    Args:
        skill_path: skill 文件路径(.md),可以是绝对或相对路径。

    Returns:
        skill 文件的 UTF-8 解码后的纯文本。

    Raises:
        FileNotFoundError: 文件不存在。
        IsADirectoryError: 路径是目录。
    """
    path = Path(skill_path)
    if not path.exists():
        raise FileNotFoundError(f"Skill 文件不存在: {path}")
    if path.is_dir():
        raise IsADirectoryError(f"Skill 路径是目录而非文件: {path}")
    return path.read_text(encoding="utf-8")


def load_skill_by_name(skill_name: str) -> str:
    """根据名称从内置 skills 目录加载 skill(去掉 .md 后缀)。"""
    path = SKILLS_DIR / f"{skill_name}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"Skill {skill_name!r} 不存在,期望路径 {path}。"
            f"可用 skill 文件: {[p.name for p in SKILLS_DIR.glob('*.md')]}"
        )
    return load_skill(path)


def run_with_skill(
    task: str,
    skill_content: str | None,
    *,
    model: str | None = None,
    client: DeepSeekClient | None = None,
    skill_name: str | None = None,
) -> RunOutput:
    """在指定 skill 引导下执行任务。

    关键设计:
    - skill 作为 system message 注入,保留任务的"风格指南"。
    - skill_content=None 时退化为裸 prompt(基线对照)。
    - user message 就是 task 本身,无额外包装,方便后续评测维度对齐。

    Args:
        task: 任务 prompt。
        skill_content: skill 文件内容(可为 None,代表无 skill 基线)。
        model: 模型覆盖,默认使用 client.settings.execute_model。
        client: 注入的 DeepSeek 客户端(便于测试);None 时按需构造。
        skill_name: 用于在产物里标注当前使用的 skill 名称(可读性)。

    Returns:
        RunOutput,包含产物文本与 token 统计。
    """
    if not task or not task.strip():
        raise ValueError("task 不能为空")

    messages: list[dict[str, str]] = []
    if skill_content is not None and skill_content.strip():
        messages.append({"role": "system", "content": skill_content})
    messages.append({"role": "user", "content": task})

    client = client or DeepSeekClient()
    result: CompletionResult = client.execute(messages, model=model)

    return RunOutput(
        skill_name=skill_name,
        task=task,
        content=result.content,
        tokens=result.total_tokens,
        model=result.model,
    )


def list_available_skills(skills_dir: Path | None = None) -> list[str]:
    """列出内置 skills 目录下的所有 skill 名称(不含扩展名)。"""
    base = skills_dir or SKILLS_DIR
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.md"))


__all__ = [
    "RunOutput",
    "load_skill",
    "load_skill_by_name",
    "run_with_skill",
    "list_available_skills",
]