"""Skill 融合引擎:取两个 skill 的优点,生成 v3。

设计要点:
- 输入:两个 skill 全文 + 它们在对比中的表现(judge 给出的 strengths/weaknesses)
  + 任务上下文。
- 输出:一份新的 skill 文档(markdown 格式),保留 A 强项 + B 强项,避免
  A、B 各自的弱项;严格按 markdown 结构输出(标题、核心原则、行为约束、示例)。
- 内部 prompt 显式约束:
  * 保留 A 的强项 + B 的强项
  * 避免 A 和 B 的弱项
  * markdown 格式(标题、核心原则、行为约束、示例)
  * 长度 150-400 字
- 失败处理:若模型返回非预期格式,自动重试一次;仍然失败则 raise 上下文异常。
- 复用 `arena.deepseek_client.DeepSeekClient.execute`,不直接调 OpenAI。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .deepseek_client import CompletionResult, DeepSeekClient

logger = logging.getLogger(__name__)


# -------- 长度约束 --------
FUSE_MIN_LENGTH: int = 150
FUSE_MAX_LENGTH: int = 400


# -------- 提示词模板 --------

FUSE_SYSTEM_PROMPT = """你是 skill 设计专家,擅长把多个 skill 的优点融合成更优的版本。

你的任务:基于给定的两个 skill A、B 及其对比反馈,生成一份新的 skill(v3)。
"""


FUSE_USER_PROMPT_TEMPLATE = """## 任务上下文
{task_context}

## Skill A ({skill_a_name})
{skill_a_content}

## Skill B ({skill_b_name})
{skill_b_content}

## 评判反馈
{judge_feedback}

## 你的产出要求
请基于以上信息,融合两个 skill 的优点,生成 v3 版本的 skill 文档。
严格遵循以下约束:

1. **保留 A 的强项**:从 Skill A 中识别被评判标记为优秀的部分,必须保留。
2. **保留 B 的强项**:从 Skill B 中识别被评判标记为优秀的部分,必须保留。
3. **避免 A 的弱项**:不要重复 A 中被批评的写法或风格。
4. **避免 B 的弱项**:不要重复 B 中被批评的写法或风格。
5. **输出严格 markdown 格式**,包含以下结构:
   - 标题(H1):`# <skill 名称>`
   - 核心原则(H2):`## 核心原则`,3-5 条编号列表
   - 行为约束(H2):`## 行为约束`,1-3 条不允许的写法
   - 示例(H2):`## 示例`,1 段简短的输入-输出示例
6. **总长度 150-400 字**(中文字符计,不含标题前缀)。
7. **不要在文档前后添加任何额外解释、注释或 markdown 围栏**(如 ```markdown)。
   只输出 skill 文档本体。
"""


# -------- 结构化解析 --------

_FENCE_RE = re.compile(r"```(?:markdown|md)?\s*\n(.*?)\n```", re.DOTALL)


class _FuseOutput(BaseModel):
    """融合产物的最简结构化校验,确保至少有标题、核心原则、行为约束、示例。"""

    title: str
    has_core_principles: bool
    has_behavior_constraints: bool
    has_example: bool
    body: str

    @classmethod
    def parse(cls, text: str) -> "_FuseOutput":
        """从模型输出中解析融合结果。

        行为:
        1. 去除首尾空白。
        2. 若首行以 markdown 围栏(```markdown / ```md)包裹,先剥除。
        3. 必须包含 H1 标题(`# xxx`)。
        4. 检查 H2 小节:核心原则、行为约束、示例,任意一个缺失即视为不合格。
        """
        body = text.strip()
        if not body:
            raise ValueError("模型返回为空")

        # 剥除 markdown 围栏
        m = _FENCE_RE.search(body)
        if m:
            body = m.group(1).strip()

        # 必须包含 H1 标题
        h1_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if not h1_match:
            raise ValueError("模型输出缺少 H1 标题(`# xxx`)")
        title = h1_match.group(1).strip()

        sections = {
            "core_principles": bool(
                re.search(r"^##\s*核心原则", body, re.MULTILINE)
            ),
            "behavior_constraints": bool(
                re.search(r"^##\s*行为约束", body, re.MULTILINE)
            ),
            "example": bool(re.search(r"^##\s*示例", body, re.MULTILINE)),
        }

        return cls(
            title=title,
            has_core_principles=sections["core_principles"],
            has_behavior_constraints=sections["behavior_constraints"],
            has_example=sections["example"],
            body=body,
        )


# -------- 内部辅助 --------

def _strip_excess_sections(body: str) -> str:
    """超出 4 个 H2 章节时,丢弃多余章节,防止模型跑题。

    实现:按 H2 标题分块,保留前 4 个(标题、核心原则、行为约束、示例),
    其余从第一个不期望章节开始截断。
    """
    expected = ("## 核心原则", "## 行为约束", "## 示例")
    # 找出所有 H2 标题位置
    h2_positions = [
        m.start() for m in re.finditer(r"^##\s+", body, re.MULTILINE)
    ]
    if not h2_positions:
        return body  # 没有 H2 不截断,留给调用方判定

    # 找到第一个不在 expected 中的 H2 位置
    cut_at: int | None = None
    for pos in h2_positions:
        # 取这一行的标题文本
        line_end = body.find("\n", pos)
        line = body[pos:line_end if line_end != -1 else None].strip()
        if not any(line.startswith(e) for e in expected):
            cut_at = pos
            break
    if cut_at is not None:
        body = body[:cut_at].rstrip() + "\n"
    return body


# -------- 主入口 --------

def fuse_skills(
    skill_a_content: str,
    skill_a_name: str,
    skill_b_content: str,
    skill_b_name: str,
    task_context: str,
    judge_feedback: str,
    model: str = "deepseek-v4-pro",
    *,
    client: DeepSeekClient | None = None,
) -> str:
    """融合两个 skill,生成 v3。

    Args:
        skill_a_content: skill A 全文(必填)。
        skill_a_name: skill A 名称(用于 prompt 标注)。
        skill_b_content: skill B 全文(必填)。
        skill_b_name: skill B 名称。
        task_context: 任务上下文(例如任务类型、典型场景)。
        judge_feedback: 评判反馈(包含 strengths/weaknesses)。可为空字符串。
        model: 模型名,默认 "deepseek-v4-pro"(占位,可被 settings.execute_model 覆盖)。
        client: 注入的 DeepSeekClient(便于测试);None 时按需构造。

    Returns:
        融合后的新 skill 文档(纯 markdown 文本,可直接落盘为 .md)。

    Raises:
        ValueError: 输入参数非法(skill 内容为空等)。
        RuntimeError: 模型两次调用均未产出合格 skill(带上下文的异常)。
    """
    if not skill_a_content or not skill_a_content.strip():
        raise ValueError("skill_a_content 不能为空")
    if not skill_b_content or not skill_b_content.strip():
        raise ValueError("skill_b_content 不能为空")
    if not task_context or not task_context.strip():
        raise ValueError("task_context 不能为空")

    # judge_feedback 可为空字符串;为 None 时也兜底为空字符串
    feedback = (judge_feedback or "").strip() or "(无评判反馈)"

    messages = _build_messages(
        skill_a_content=skill_a_content,
        skill_a_name=skill_a_name or "Skill A",
        skill_b_content=skill_b_content,
        skill_b_name=skill_b_name or "Skill B",
        task_context=task_context,
        judge_feedback=feedback,
    )

    client = client or DeepSeekClient()

    # 第一次尝试
    first = _call_model(client, messages, model=model)
    try:
        return _finalize(first, context=(_summarize_inputs(skill_a_name, skill_b_name, feedback)))
    except ValueError as exc:
        logger.warning("融合产物首次解析失败,尝试一次修复 prompt: %s", exc)
        # 修复 prompt:追加一段强约束指令
        fix_messages = list(messages) + [
            {
                "role": "user",
                "content": (
                    "你上一轮的输出不符合要求:缺少必需的 H2 小节或 markdown 结构不规范。"
                    "请重新生成一份**严格的 markdown 文档**,必须包含且只包含以下 H2 小节(顺序不限):"
                    "`## 核心原则` / `## 行为约束` / `## 示例`。"
                    "必须以 `# <标题>` 开头。不要任何解释或围栏。"
                ),
            }
        ]
        second = _call_model(client, fix_messages, model=model)
        try:
            return _finalize(
                second,
                context=(_summarize_inputs(skill_a_name, skill_b_name, feedback)),
            )
        except ValueError as exc2:
            raise RuntimeError(
                f"融合两次解析均失败: first={exc!r}; second={exc2!r}; "
                f"first_raw={first[:200]!r}; second_raw={second[:200]!r}"
            ) from exc2


# -------- 内部小工具 --------

def _build_messages(
    *,
    skill_a_content: str,
    skill_a_name: str,
    skill_b_content: str,
    skill_b_name: str,
    task_context: str,
    judge_feedback: str,
) -> list[dict[str, str]]:
    """构造发往模型的 messages 列表。"""
    user_prompt = FUSE_USER_PROMPT_TEMPLATE.format(
        task_context=task_context.strip(),
        skill_a_name=skill_a_name,
        skill_a_content=skill_a_content.strip(),
        skill_b_name=skill_b_name,
        skill_b_content=skill_b_content.strip(),
        judge_feedback=judge_feedback,
    )
    return [
        {"role": "system", "content": FUSE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _call_model(
    client: DeepSeekClient,
    messages: list[dict[str, str]],
    *,
    model: str,
) -> str:
    """调一次模型,返回 content。融合用 judge 方法(低 temperature,更稳定)。
    失败时回退到 settings.execute_model。"""
    try:
        result: CompletionResult = client.judge(messages, model=model)
    except Exception as exc:  # noqa: BLE001
        try:
            fallback = client.settings.judge_model
            result = client.judge(messages, model=fallback)
        except Exception as exc2:  # noqa: BLE001
            try:
                fallback2 = client.settings.execute_model
                result = client.execute(messages, model=fallback2)
            except Exception as exc3:  # noqa: BLE001
                raise RuntimeError(
                    f"融合模型调用失败(指定={model}): {exc!r}; "
                    f"judge备选={client.settings.judge_model}: {exc2!r}; "
                    f"execute备选={client.settings.execute_model}: {exc3!r}"
                ) from exc3
    return result.content


def _summarize_inputs(
    skill_a_name: str, skill_b_name: str, feedback: str
) -> dict[str, Any]:
    """生成一个简短的输入摘要,用于错误上下文。"""
    return {
        "skill_a": skill_a_name,
        "skill_b": skill_b_name,
        "feedback_len": len(feedback),
    }


def _finalize(text: str, *, context: dict[str, Any]) -> str:
    """结构化校验 + 长度裁剪 + 返回最终 skill 文本。

    行为(按顺序):
    1. 解析结构:必须有 H1 标题 + 核心原则/行为约束/示例 三个 H2。
       任意一个缺失 → raise ValueError,触发 fuse_skills 内的修复重试。
    2. 移除多余 H2 章节,保留前 3 个必需 H2(详见 _strip_excess_sections)。
    3. 长度硬约束:去除空白后的字符数必须 ∈ [FUSE_MIN_LENGTH, FUSE_MAX_LENGTH]。
       - 偏短(<FUSE_MIN_LENGTH):视为"模型未产出合格 skill",
         raise ValueError 触发修复重试,而不是静默通过。
       - 偏长(>FUSE_MAX_LENGTH):按"非空白字符数"截断到 FUSE_MAX_LENGTH
         (不是按原始字符数);截断后必须**重新校验必需 H2 仍存在**
         —— 若必需要节被截断,raise ValueError 触发重试(模型把字数
         全堆在前 1-2 个章节上,需要被惩罚)。
    4. 返回纯 markdown 文本。

    Raises:
        ValueError: 章节缺失、偏短、截断导致必需要节丢失。
    """
    parsed = _FuseOutput.parse(text)
    if not (
        parsed.has_core_principles
        and parsed.has_behavior_constraints
        and parsed.has_example
    ):
        missing = [
            n
            for n, ok in (
                ("核心原则", parsed.has_core_principles),
                ("行为约束", parsed.has_behavior_constraints),
                ("示例", parsed.has_example),
            )
            if not ok
        ]
        raise ValueError(
            f"融合产物缺少必需章节: {missing}; context={context}"
        )

    body = _strip_excess_sections(parsed.body)

    # ---- 长度硬约束(按去除空白后的字符数计)----
    compact = re.sub(r"\s+", "", body)
    compact_len = len(compact)
    if compact_len < FUSE_MIN_LENGTH:
        # 偏短 → 视为不合规,触发 fuse_skills 的修复重试
        raise ValueError(
            f"融合产物偏短(compact_len={compact_len} < FUSE_MIN_LENGTH={FUSE_MIN_LENGTH});"
            f" context={context}"
        )

    if compact_len > FUSE_MAX_LENGTH:
        # 偏长 → 按"非空白字符数"截断到 FUSE_MAX_LENGTH
        # 实现:在原始 body 上走一遍,每遇到一个非空白字符就计数,
        # 达到 FUSE_MAX_LENGTH 时切到该位置;最后保留末尾换行让 markdown
        # 渲染友好。
        kept = 0
        cut = len(body)
        for i, ch in enumerate(body):
            if not ch.isspace():
                kept += 1
                if kept >= FUSE_MAX_LENGTH:
                    cut = i + 1
                    break
        body = body[:cut].rstrip() + "\n"

        # 截断后必须重新校验:必需要节必须仍存在。
        # 若被截断丢弃,意味着模型把字数全堆在前 1-2 个章节,
        # 应触发 fuse_skills 内的修复重试。
        for marker in ("## 核心原则", "## 行为约束", "## 示例"):
            if marker not in body:
                raise ValueError(
                    f"融合产物截断后丢失必需要节 {marker!r};"
                    f" 截断点不足以容纳所有 H2,触发重试; context={context}"
                )

        # 防御性:截断后再次校验长度区间
        compact = re.sub(r"\s+", "", body)
        if not (FUSE_MIN_LENGTH <= len(compact) <= FUSE_MAX_LENGTH):
            raise ValueError(
                f"融合产物截断后长度仍不合规(len={len(compact)});"
                f" context={context}"
            )

    return body


__all__ = [
    "FUSE_MIN_LENGTH",
    "FUSE_MAX_LENGTH",
    "FUSE_SYSTEM_PROMPT",
    "FUSE_USER_PROMPT_TEMPLATE",
    "fuse_skills",
]
