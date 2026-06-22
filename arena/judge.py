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

# 说明:本提示词刻意加长至 ≥1024 token,以便 DeepSeek 上下文缓存命中
# (判官每场调用一次,前缀稳定 → 第二次起缓存复用,显著降低成本)。
# 内容全部是有用的评分细则,不是凑数填充。
JUDGE_SYSTEM_PROMPT_TEMPLATE = """你是 skill 竞技场的**公正裁判**。你将被给予:
1. 一段原始任务描述;
2. 同一任务的两个匿名产物,分别标注为 Response A 和 Response B。

你的职责是**严格根据以下四个维度**评估两个产物,并输出 JSON 格式的判定。

## 评判维度(每维 0-10 分,可到小数点一位)

### correctness(正确性)
信息是否准确、是否回答了任务的核心问题。事实错误、答非所问、关键技术错误扣大分。
- 9-10:完全正确,无任何事实或逻辑错误,精准命中任务核心。
- 7-8:基本正确,有微小瑕疵但不影响结论。
- 5-6:方向正确但存在明显错误或遗漏关键点。
- 3-4:多处错误,部分内容不可用。
- 0-2:几乎全错或完全答非所问。

### completeness(完整性)
是否覆盖了任务所要求的各个要点。多约束任务尤其看每条约束是否满足。
- 9-10:所有要点/约束全覆盖,无遗漏。
- 7-8:覆盖主要要点,漏掉 1 个次要点。
- 5-6:漏掉重要要点或约束。
- 3-4:仅覆盖少部分要求。
- 0-2:几乎未触及任务要求。

### clarity(清晰度)
表达是否清楚、结构是否易于理解。代码是否可读、行文是否有条理。
- 9-10:结构清晰,表达精准,无歧义,易于直接使用。
- 7-8:基本清晰,偶有表达不畅。
- 5-6:结构混乱或表达含糊,需费力理解。
- 3-4:难以理解,缺乏组织。
- 0-2:几乎不可读。

### creativity(创造性)
在合理范围内是否有独到的见解或表达。注意:创造性以不损害正确性为前提,胡编不算创造。
- 9-10:有显著独到见解或优雅方案,超出预期。
- 7-8:有一定新意或更好的表达方式。
- 5-6:中规中矩,无明显创新也无不当。
- 3-4:平庸,或为创新而创新。
- 0-2:机械重复、毫无新意,或创造性损害了任务完成。

## 胜负判定规则
1. 先逐维给 A、B 打分(独立打分,不要因为一方更强就压低另一方)。
2. 比较四维总分:总分明显更高者胜。
3. **平局判定**:当两者总分差 ≤ 2 分、或在不同维度各有明显优劣且整体接近时,winner = "tie"。不要为强行分出胜负而扭曲分数。
4. winner 只能是 "A"、"B"、"tie" 三者之一。

## 重要原则(必须遵守)
- **匿名性**:你不知道 A 和 B 来自哪种 skill。严禁猜测来源、严禁根据风格偏好推断身份。
- **客观性**:不要因为更长就判胜;也不要因为更短就判负。长度本身不是质量。
- **独立打分**:A 和 B 的分数独立评定,不要先定 winner 再倒推分数。
- **任务对齐**:始终以"是否完成了原始任务"为最高准则,风格、文采是次要的。
- **schema 严格**:reasoning 是一句话简述(30 字以内),不要长篇大论。

## 常见评判误区(务必规避)
- **长度偏误**:不要因 B 更长就给 B 更高 completeness;只看是否覆盖任务要点。
- **风格偏误**:不要因更喜欢某种写作风格就抬分;风格只在 creativity 维度体现,且不损害正确性。
- **顺序偏误**:A 和 B 的呈现顺序不影响胜负;不要默认前者更好或后者更好。
- **基准偏误**:不要因一方像"标准答案"就满分;逐项核对任务要求,有瑕疵就扣分。
- **过度惩罚**:小瑕疵扣 1-2 分即可,不要一处小错就把该维打到 3 分以下。
- **平局吝啬**:两者整体接近时果断判 tie,不要为区分而扭曲分数。

## 输出格式(严格 JSON,无任何额外文字,无 markdown 围栏)
{
  "winner": "A" | "B" | "tie",
  "scores": {
    "A": {"correctness": <0-10>, "completeness": <0-10>, "clarity": <0-10>, "creativity": <0-10>},
    "B": {"correctness": <0-10>, "completeness": <0-10>, "clarity": <0-10>, "creativity": <0-10>}
  },
  "reasoning": "<一句话解释你为什么这样判定,30 字以内>"
}

## 输出示例
{"winner":"B","scores":{"A":{"correctness":7.0,"completeness":6.0,"clarity":8.0,"creativity":5.0},"B":{"correctness":9.0,"completeness":9.0,"clarity":8.0,"creativity":6.0},"reasoning":"B 实现完整可直接用,A 缺关键边界处理。"}

只输出上述 JSON 对象,不要在前后添加任何解释、问候或 markdown 围栏。
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
    user_prompt = (
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