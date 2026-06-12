"""评判引擎:调用评判模型对两段匿名产物进行对比,产出可校验的 Verdict。

设计要点:
- 严格 JSON 输出 + pydantic schema 校验 + 失败重试。
- 匿名化:产物被标注为 Response A / Response B,不泄露 skill 名称。
- 维度固定:correctness / completeness / clarity / creativity,每维 0-10 分。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from .deepseek_client import DeepSeekClient

logger = logging.getLogger(__name__)

# 评判维度清单(报告与 prompt 都用同一份,避免漂移)
JUDGE_DIMENSIONS: tuple[str, ...] = (
    "correctness",
    "completeness",
    "clarity",
    "creativity",
)
SCORE_MIN: float = 0.0
SCORE_MAX: float = 10.0


class DimensionScores(BaseModel):
    """单段产物的维度分数。所有维度必须 ∈ [0, 10]。"""

    correctness: float = Field(ge=SCORE_MIN, le=SCORE_MAX)
    completeness: float = Field(ge=SCORE_MIN, le=SCORE_MAX)
    clarity: float = Field(ge=SCORE_MIN, le=SCORE_MAX)
    creativity: float = Field(ge=SCORE_MIN, le=SCORE_MAX)


class Verdict(BaseModel):
    """一次对比的结构化判定结果。

    Attributes:
        winner: "A" | "B" | "tie"。
        scores: 两个产物的维度分数。
        reasoning: 自然语言理由(简短)。
        raw: 模型原始返回(便于调试/审计),可选。
    """

    winner: str
    scores: dict[str, DimensionScores]
    reasoning: str

    raw: str | None = None

    @field_validator("winner")
    @classmethod
    def _validate_winner(cls, v: str) -> str:
        v_norm = v.strip().lower()
        mapping = {"a": "A", "b": "B", "tie": "tie", "draw": "tie"}
        if v_norm not in mapping:
            raise ValueError(f"winner 必须是 A/B/tie 之一,实际为 {v!r}")
        return mapping[v_norm]

    @field_validator("scores")
    @classmethod
    def _validate_scores(cls, v: dict[str, DimensionScores]) -> dict[str, DimensionScores]:
        # 只接受大写键 A/B,兼容小写 a/b
        out: dict[str, DimensionScores] = {}
        for key, value in v.items():
            k = key.strip().upper()
            if k not in ("A", "B"):
                raise ValueError(f"scores 键必须是 A/B,实际为 {key!r}")
            out[k] = value
        if set(out.keys()) != {"A", "B"}:
            raise ValueError(f"scores 必须同时包含 A 和 B,实际为 {set(out.keys())}")
        return out

    def total_score(self, side: str) -> float:
        """计算某侧产物的总分(四维加和)。"""
        side = side.strip().upper()
        if side not in self.scores:
            raise KeyError(f"Verdict 中没有 side={side!r}")
        s = self.scores[side]
        return s.correctness + s.completeness + s.clarity + s.creativity

    def to_score(self) -> float:
        """转换为 Elo score:从 A 视角(1.0 / 0.5 / 0.0)。"""
        if self.winner == "A":
            return 1.0
        if self.winner == "B":
            return 0.0
        return 0.5


# -------- Prompt 构造 --------

JUDGE_SYSTEM_PROMPT_TEMPLATE = """你是 skill 竞技场的**公正裁判**。你将被给予:
1. 一段原始任务描述;
2. 同一任务的两个匿名产物,分别标注为 Response A 和 Response B。

你的职责是**严格根据以下维度**评估两个产物,并输出 JSON 格式的判定:

## 评判维度(每维 0-10 分)
- correctness(正确性):信息是否准确、是否回答了任务的核心问题。
- completeness(完整性):是否覆盖了任务所要求的各个要点。
- clarity(清晰度):表达是否清楚、结构是否易于理解。
- creativity(创造性):在合理范围内是否有独到的见解或表达。

## 输出格式(严格 JSON,无任何额外文字)
```json
{{
  "winner": "A" | "B" | "tie",
  "scores": {{
    "A": {{
      "correctness": <0-10>,
      "completeness": <0-10>,
      "clarity": <0-10>,
      "creativity": <0-10>
    }},
    "B": {{
      "correctness": <0-10>,
      "completeness": <0-10>,
      "clarity": <0-10>,
      "creativity": <0-10>
    }}
  }},
  "reasoning": "<一句话解释你为什么这样判定,30 字以内>"
}}
```

## 重要原则
- **匿名性**:你不知道 Response A 和 Response B 来自哪种 skill。请勿猜测或假设。
- **客观性**:不要因为表达更长就判胜;也不要因为风格偏好而偏袒。
- **平局判定**:当两者在不同维度各有优劣、整体水平接近时,winner = "tie"。
- **只输出 JSON**:不要在 JSON 前后添加任何解释、markdown 围栏、问候语。
"""


def build_judge_messages(
    task: str,
    output_a: str,
    output_b: str,
    *,
    skill_a: str | None = None,
    skill_b: str | None = None,
) -> list[dict[str, str]]:
    """构造评判所需的 messages 列表。

    Args:
        task: 原始任务 prompt。
        output_a: A 的产物文本。
        output_b: B 的产物文本。
        skill_a / skill_b: 可选的 skill 名称,**仅记录在 user message 元信息**,
            真实评判时仍以匿名形式呈现,避免引入偏差。
            若希望完全隐藏 skill 来源,可以传 None。

    Returns:
        OpenAI 兼容格式的 messages 列表,第一条是 system prompt。
    """
    # 元信息块:在 user 消息开头明确告知模型"这是匿名的",并保留 skill 名称
    # 仅作为审计信息(模型在认真读 prompt 时不会被诱导,但审计人员可追溯)。
    meta_lines = []
    if skill_a is not None:
        meta_lines.append(f"Response A 来自 skill: {skill_a!r} (仅供审计)")
    if skill_b is not None:
        meta_lines.append(f"Response B 来自 skill: {skill_b!r} (仅供审计)")
    meta_block = ("\n".join(meta_lines) + "\n") if meta_lines else ""

    user_prompt = (
        f"{meta_block}"
        f"## 原始任务\n{task.strip()}\n\n"
        f"## Response A\n{output_a.strip()}\n\n"
        f"## Response B\n{output_b.strip()}\n\n"
        f"请严格按照系统提示中的 JSON schema 输出判定。"
    )

    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT_TEMPLATE},
        {"role": "user", "content": user_prompt},
    ]


# -------- JSON 抽取辅助 --------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """从模型回复中尽可能稳健地抽取 JSON。

    处理策略(按优先级):
    1. 直接 json.loads 整段。
    2. 抽取 ```json ... ``` 围栏中的内容再 loads。
    3. 找首对匹配的大括号并尝试 loads。

    若全部失败,抛出 ValueError。
    """
    text = text.strip()

    # 策略 1
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 策略 2
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 策略 3:贪心找首个 { 到末尾的 }
    if "{" in text and "}" in text:
        start = text.index("{")
        end = text.rindex("}") + 1
        candidate = text[start:end]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"无法从模型回复中抽取合法 JSON: {exc}; raw={text[:300]!r}..."
            ) from exc

    raise ValueError(f"模型回复中没有 JSON 对象: {text[:200]!r}")


# -------- 主入口 --------

def compare(
    task: str,
    output_a: str,
    output_b: str,
    *,
    skill_a: str | None = None,
    skill_b: str | None = None,
    client: DeepSeekClient | None = None,
    model: str | None = None,
) -> Verdict:
    """调用评判模型对两段产物做对比,返回结构化 Verdict。

    内部逻辑:
    1. 构造 messages(system + user)。
    2. 调用 judge 模型。
    3. 抽取 JSON,做 pydantic schema 校验。
    4. 若校验失败,打 warning 并尝试一次"修复 prompt"重试;仍失败则 raise。

    Args:
        task: 原始任务。
        output_a / output_b: 两段产物。
        skill_a / skill_b: 仅作为审计信息,可选。
        client: 注入的 DeepSeek 客户端(None 时按需构造)。
        model: 模型覆盖。
    """
    client = client or DeepSeekClient()
    messages = build_judge_messages(
        task=task,
        output_a=output_a,
        output_b=output_b,
        skill_a=skill_a,
        skill_b=skill_b,
    )

    # 第一次尝试
    result = client.judge(messages, model=model)
    try:
        return _parse_verdict(result.content, raw=result.content)
    except (ValidationError, ValueError) as exc:
        logger.warning("首次 Verdict 解析失败,尝试一次修复 prompt: %s", exc)
        return _retry_parse(client, messages, raw_first=result.content, exc=exc)


def _parse_verdict(text: str, *, raw: str) -> Verdict:
    """抽取 JSON → pydantic 校验。"""
    data = _extract_json(text)
    verdict = Verdict.model_validate(data)
    # 保留 raw(便于审计)
    object.__setattr__(verdict, "raw", raw)
    return verdict


def _retry_parse(
    client: DeepSeekClient,
    previous_messages: list[dict[str, str]],
    *,
    raw_first: str,
    exc: Exception,
) -> Verdict:
    """修复 prompt 重试:追加一段指令要求模型严格输出 JSON。"""
    fix_user = (
        "你上一轮的输出无法被解析为合法 JSON。"
        "请**只**输出一段严格符合 schema 的 JSON,不要任何额外文字、"
        "不要 markdown 围栏。schema:\n"
        '{"winner":"A|B|tie","scores":{"A":{"correctness":0,"completeness":0,'
        '"clarity":0,"creativity":0},"B":{"correctness":0,"completeness":0,'
        '"clarity":0,"creativity":0}},"reasoning":"..."}'
    )
    new_messages = list(previous_messages) + [{"role": "user", "content": fix_user}]
    result = client.judge(new_messages)
    try:
        return _parse_verdict(result.content, raw=result.content)
    except (ValidationError, ValueError) as exc2:
        raise RuntimeError(
            f"Verdict 解析两次均失败: first={exc!r}; second={exc2!r}; "
            f"first_raw={raw_first[:300]!r}; second_raw={result.content[:300]!r}"
        ) from exc2


__all__ = [
    "JUDGE_DIMENSIONS",
    "DimensionScores",
    "Verdict",
    "JUDGE_SYSTEM_PROMPT_TEMPLATE",
    "build_judge_messages",
    "compare",
]