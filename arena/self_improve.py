"""Skill 自改进引擎:基于评判反馈迭代优化 skill。

设计要点:
- `improve_skill`:接受当前 skill + 它在最近 N 场 Elo 对战中被 pro 标记的
  weakness 列表,产出改进后的新 skill 文本。
  * 针对每个 weakness 给具体修改建议(在内部 prompt 显式要求)
  * 输出完整的新 skill 文本(不是 patch / diff)
  * 必须保留原 skill 中未被批评的部分

- `run_improvement_cycle`:循环改进入口。
  * 流程:评估当前 Elo → 收集 weaknesses → improve → 用新 skill 重跑 N 场 →
    看 Elo 提升 → 达到 target_elo_delta 或 max_iterations 停止
  * 依赖注入:为便于单测,所有"评估"和"重跑"都通过 callback 注入:
    - `evaluator(skill_content, skill_name) -> tuple[float, list[str]]` 返回 (Elo, weaknesses)
    - 默认实现是 `lambda c, n: (1500.0, [])`,便于测试
    - 生产环境由 orchestrator 注入真实实现(用 run_with_skill + compare + elo)

- 复用 `arena.deepseek_client.DeepSeekClient.execute` 和 `arena.fuse.fuse_skills`
  中未涉及的"改进"prompt 模式。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from .deepseek_client import CompletionResult, DeepSeekClient

logger = logging.getLogger(__name__)


# -------- 提示词模板 --------

IMPROVE_SYSTEM_PROMPT = """你是 skill 设计专家,擅长根据评判反馈对 skill 做有针对性的改进,
同时保留原 skill 中被验证有效的部分。
"""


IMPROVE_USER_PROMPT_TEMPLATE = """## 当前 skill ({skill_name})
{skill_content}

## 评判反馈:被标记的弱点列表
{weaknesses_block}

## 你的任务
针对以上每一条 weakness,给出**具体的修改建议**,然后输出**完整的新 skill 文档**(不是 patch、不是 diff)。

要求:
1. **逐条处理**:每条 weakness 都要有对应的修改。
2. **保留未批评的部分**:原 skill 中没有出现在 weaknesses 列表里的原则、约束、风格,必须保留。
3. **输出严格 markdown 格式**,包含:
   - 标题(H1):`# <skill 名称>`
   - 核心原则(H2):`## 核心原则`,编号列表
   - 行为约束(H2):`## 行为约束`,列出不允许的写法
   - 示例(H2):`## 示例`(可选,但推荐)
4. **不要在文档前后添加任何额外解释、注释或 markdown 围栏**。
   只输出 skill 文档本体。
5. **总长度 150-400 字**。
"""


# -------- 结构化解析 --------

class _ImprovedOutput(BaseModel):
    """改进产物的最简结构化校验。"""

    title: str
    has_core_principles: bool
    has_behavior_constraints: bool
    body: str

    @classmethod
    def parse(cls, text: str) -> "_ImprovedOutput":
        body = text.strip()
        if not body:
            raise ValueError("模型返回为空")

        # 剥除 markdown 围栏
        m = re.search(r"```(?:markdown|md)?\s*\n(.*?)\n```", body, re.DOTALL)
        if m:
            body = m.group(1).strip()

        h1_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if not h1_match:
            raise ValueError("改进产物缺少 H1 标题")
        title = h1_match.group(1).strip()

        return cls(
            title=title,
            has_core_principles=bool(
                re.search(r"^##\s*核心原则", body, re.MULTILINE)
            ),
            has_behavior_constraints=bool(
                re.search(r"^##\s*行为约束", body, re.MULTILINE)
            ),
            body=body,
        )


# -------- 数据类 --------

@dataclass(frozen=True)
class ImprovementStep:
    """自改进循环中一轮的快照。"""

    iteration: int
    skill_version: str  # 改进后写入 / 内存中的 skill 文本
    elo_before: float
    elo_after: float
    elo_delta: float
    weaknesses: tuple[str, ...]


@dataclass(frozen=True)
class ImprovementReport:
    """自改进循环的完整报告。"""

    skill_name: str
    steps: tuple[ImprovementStep, ...]
    final_elo: float
    converged: bool
    total_iterations: int
    notes: str = ""

    def best_elo(self) -> float:
        """所有轮次(含初始 0 轮的初始 Elo)中的最高 Elo。"""
        if not self.steps:
            return 1500.0
        return max(s.elo_after for s in self.steps)


# -------- 主入口:单次改进 --------

def improve_skill(
    skill_content: str,
    skill_name: str,
    weaknesses: list[str],
    model: str = "deepseek-v4-pro",
    *,
    client: DeepSeekClient | None = None,
) -> str:
    """根据弱点列表改进 skill。

    Args:
        skill_content: 当前 skill 全文。
        skill_name: skill 名称(用于 prompt 标注与落盘命名)。
        weaknesses: 该 skill 在最近 N 场对战中暴露的弱点列表(可空)。
        model: 模型名,默认占位 "deepseek-v4-pro"。
        client: 注入的 DeepSeekClient(便于测试)。

    Returns:
        改进后的新 skill 文本(纯 markdown)。

    Raises:
        ValueError: skill_content 为空。
        RuntimeError: 模型两次调用均未产出合格 skill。
    """
    if not skill_content or not skill_content.strip():
        raise ValueError("skill_content 不能为空")
    if not skill_name or not skill_name.strip():
        raise ValueError("skill_name 不能为空")

    # 空 weaknesses:无批评则直接返回原 skill(无改进空间)
    if not weaknesses:
        logger.info(
            "improve_skill: %r 无弱点,直接返回原 skill", skill_name
        )
        return skill_content

    # 清洗 weaknesses:去空白,过滤空串
    cleaned: list[str] = [
        w.strip() for w in weaknesses if w and w.strip()
    ]
    if not cleaned:
        return skill_content

    weaknesses_block = "\n".join(f"{i + 1}. {w}" for i, w in enumerate(cleaned))
    messages = _build_messages(
        skill_name=skill_name,
        skill_content=skill_content,
        weaknesses_block=weaknesses_block,
    )

    client = client or DeepSeekClient()

    # 第一次尝试
    first = _call_model(client, messages, model=model)
    try:
        return _finalize(first, skill_name=skill_name)
    except ValueError as exc:
        logger.warning("改进产物首次解析失败,尝试一次修复 prompt: %s", exc)
        fix_messages = list(messages) + [
            {
                "role": "user",
                "content": (
                    "你上一轮的输出不符合要求:缺少 H1 标题或核心原则/行为约束 H2 小节。"
                    "请重新生成,严格以 `# <标题>` 开头,必须包含 "
                    "`## 核心原则` 和 `## 行为约束` 两个 H2 小节,"
                    "并保留原 skill 中未被批评的内容。"
                    "不要任何解释或围栏。"
                ),
            }
        ]
        second = _call_model(client, fix_messages, model=model)
        try:
            return _finalize(second, skill_name=skill_name)
        except ValueError as exc2:
            raise RuntimeError(
                f"improve_skill 两次解析均失败: first={exc!r}; second={exc2!r}; "
                f"first_raw={first[:200]!r}; second_raw={second[:200]!r}"
            ) from exc2


# -------- 主入口:循环改进 --------

# 类型:evaluator 返回 (Elo, weaknesses)
Evaluator = Callable[[str, str], tuple[float, list[str]]]


def _default_evaluator(skill_content: str, skill_name: str) -> tuple[float, list[str]]:
    """默认 evaluator:无外部评估能力时返回 (1500, [])。

    生产实现应注入真实 evaluator(在 orchestrator 任务里实现):
    - 跑 N 场对战 → 更新 Elo
    - 收集评判的 weakness 列表
    """
    return (1500.0, [])


def run_improvement_cycle(
    skill_name: str,
    skill_content: str | None = None,
    *,
    max_iterations: int = 3,
    target_elo_delta: float = 20.0,
    evaluator: Evaluator | None = None,
    model: str = "deepseek-v4-pro",
    client: DeepSeekClient | None = None,
) -> ImprovementReport:
    """对单个 skill 运行自改进循环。

    流程:
    1. 读取当前 skill(若 skill_content 为 None,尝试从 skills/{name}.md 加载)。
    2. 评估当前 Elo(evaluator 回调)。
    3. 循环 max_iterations 次:
       a. 收集 weaknesses
       b. 若 weaknesses 为空,提前停止(converged=True)
       c. 调用 improve_skill 生成新版本
       d. 重新评估新版本 Elo
       e. 若 elo_after - elo_before >= target_elo_delta,达到目标,停止
    4. 返回 ImprovementReport(每轮的版本、Elo、提升幅度)。

    Args:
        skill_name: skill 名称。
        skill_content: 当前 skill 文本;None 时尝试从 skills/{name}.md 加载。
        max_iterations: 最大循环次数(>=1)。
        target_elo_delta: 单轮 Elo 提升达到此值即停止。
        evaluator: 注入的 (skill_content, skill_name) -> (elo, weaknesses)
            回调;None 时用默认(返回 1500, [])。
        model: 模型名。
        client: 注入的 DeepSeekClient。

    Returns:
        ImprovementReport。
    """
    if max_iterations < 1:
        raise ValueError("max_iterations 必须 >= 1")

    # 1. 加载 skill 文本
    if skill_content is None:
        skill_content = _load_default_skill(skill_name)
    if not skill_content or not skill_content.strip():
        raise ValueError(f"skill_content 为空(skill_name={skill_name!r})")

    # 2. 评估初始 Elo
    ev = evaluator or _default_evaluator
    initial_elo, _ = ev(skill_content, skill_name)

    steps: list[ImprovementStep] = []
    current_skill = skill_content
    current_elo = initial_elo
    converged = False
    notes_parts: list[str] = []

    for i in range(1, max_iterations + 1):
        # 3a. 评估当前版本,获取 weaknesses
        elo_before, weaknesses = ev(current_skill, skill_name)
        if not weaknesses:
            notes_parts.append(f"iter={i} 无 weaknesses,提前停止")
            converged = True
            break

        # 3b. 改进
        try:
            new_skill = improve_skill(
                skill_content=current_skill,
                skill_name=skill_name,
                weaknesses=weaknesses,
                model=model,
                client=client,
            )
        except (RuntimeError, ValueError) as exc:
            notes_parts.append(f"iter={i} improve 失败: {exc!r}")
            # 失败则保留当前 skill,跳出循环
            break

        # 3c. 重评
        elo_after, _ = ev(new_skill, skill_name)
        elo_delta = elo_after - elo_before

        step = ImprovementStep(
            iteration=i,
            skill_version=new_skill,
            elo_before=elo_before,
            elo_after=elo_after,
            elo_delta=elo_delta,
            weaknesses=tuple(weaknesses),
        )
        steps.append(step)
        current_skill = new_skill
        current_elo = elo_after

        if elo_delta >= target_elo_delta:
            notes_parts.append(
                f"iter={i} Elo 提升 {elo_delta:.1f} >= {target_elo_delta},达成目标"
            )
            converged = True
            break

    if not converged and not notes_parts:
        notes_parts.append(
            f"达到 max_iterations={max_iterations} 仍未达成 target_elo_delta={target_elo_delta}"
        )

    return ImprovementReport(
        skill_name=skill_name,
        steps=tuple(steps),
        final_elo=current_elo,
        converged=converged,
        total_iterations=len(steps),
        notes="; ".join(notes_parts),
    )


# -------- 内部辅助 --------

def _load_default_skill(skill_name: str) -> str:
    """从 skills/{name}.md 加载默认 skill(若存在)。"""
    from pathlib import Path

    from .config import SKILLS_DIR

    path: Path = SKILLS_DIR / f"{skill_name}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"skill_name={skill_name!r} 对应文件不存在:{path}。"
            "可显式传入 skill_content 或先创建该 .md 文件。"
        )
    return path.read_text(encoding="utf-8")


def _build_messages(
    *, skill_name: str, skill_content: str, weaknesses_block: str
) -> list[dict[str, str]]:
    """构造发往模型的 messages 列表。"""
    user_prompt = IMPROVE_USER_PROMPT_TEMPLATE.format(
        skill_name=skill_name,
        skill_content=skill_content.strip(),
        weaknesses_block=weaknesses_block,
    )
    return [
        {"role": "system", "content": IMPROVE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _call_model(
    client: DeepSeekClient,
    messages: list[dict[str, str]],
    *,
    model: str,
) -> str:
    """调一次模型,失败时回退到 settings.execute_model。"""
    try:
        result: CompletionResult = client.execute(messages, model=model)
    except Exception as exc:  # noqa: BLE001
        try:
            fallback = client.settings.execute_model
            result = client.execute(messages, model=fallback)
        except Exception as exc2:  # noqa: BLE001
            raise RuntimeError(
                f"改进模型调用失败(指定={model}): {exc!r}; 备选={client.settings.execute_model}: {exc2!r}"
            ) from exc2
    return result.content


def _finalize(text: str, *, skill_name: str) -> str:
    """结构化校验 + 返回最终 skill 文本。"""
    parsed = _ImprovedOutput.parse(text)
    if not (parsed.has_core_principles and parsed.has_behavior_constraints):
        missing = [
            n
            for n, ok in (
                ("核心原则", parsed.has_core_principles),
                ("行为约束", parsed.has_behavior_constraints),
            )
            if not ok
        ]
        raise ValueError(
            f"改进产物缺少必需章节: {missing}; skill_name={skill_name!r}"
        )
    return parsed.body


__all__ = [
    "IMPROVE_SYSTEM_PROMPT",
    "IMPROVE_USER_PROMPT_TEMPLATE",
    "ImprovementStep",
    "ImprovementReport",
    "Evaluator",
    "improve_skill",
    "run_improvement_cycle",
]
