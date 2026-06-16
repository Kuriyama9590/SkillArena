"""fuse 模块单测:输入校验 / prompt 构造 / 输出解析 / 端到端(全 mock)。

所有用例都通过 monkeypatch mock 掉 DeepSeekClient 的 execute 方法,
确保不会真实发出 API 请求。
"""
from __future__ import annotations

import re
from typing import Any

import pytest

from arena.deepseek_client import CompletionResult
from arena.fuse import (
    FUSE_MAX_LENGTH,
    FUSE_MIN_LENGTH,
    FUSE_USER_PROMPT_TEMPLATE,
    fuse_skills,
)


# -------- 公共假 client --------

class _FakeDeepSeekClient:
    """可记录调用、预设返回的 DeepSeekClient 替身。"""

    def __init__(self, response_text: str | None = None) -> None:
        self.response_text = response_text or ""
        self.calls: list[list[dict[str, str]]] = []
        self._responses: list[str] = []
        if response_text is not None:
            self._responses.append(response_text)

    def execute(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> CompletionResult:
        self.calls.append(list(messages))
        if self._responses:
            text = self._responses.pop(0)
        else:
            text = self.response_text
        return CompletionResult(
            content=text,
            prompt_tokens=50,
            completion_tokens=80,
            total_tokens=130,
            model=model or "fake-execute",
        )

    def judge(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> CompletionResult:
        return self.execute(messages, model=model, temperature=temperature)

    @property
    def settings(self) -> Any:  # noqa: D401
        """返回轻量 settings 替身,支持 .execute_model 属性。"""

        class _S:
            execute_model = "fake-execute"
            judge_model = "fake-judge"

        return _S()


def _good_fused_skill(name: str = "Hybrid Writer") -> str:
    """构造一份合规的融合产物(覆盖所有 3 个必需 H2 + 长度区间内)。"""
    body = (
        f"# {name}\n\n"
        "## 核心原则\n"
        "1. 第一句话直接亮观点,不要铺垫,直入主题。\n"
        "2. 每段只表达一个核心想法,避免一锅炖,层次清晰。\n"
        "3. 短句优先,主动语态;能用一句话说清的不写两句话。\n"
        "4. 关键论点必须给一个具体例子:数字、场景或引语。\n"
        "5. 多角度展开:从利弊、对比、历史与未来至少给出两个维度。\n\n"
        "## 行为约束\n"
        "- 禁止套话开头,例如在当今社会、随着时代发展。\n"
        "- 禁止重复论点,反复复述同一结论。\n"
        "- 禁止空泛例子,例如比如某些情况下,例子必须具体。\n\n"
        "## 示例\n"
        "输入:用一段话解释设计模式,并给一个具体例子。\n"
        "输出:设计模式是反复出现的问题的可复用解。"
        "例如观察者模式:对象状态变化时自动通知所有依赖它的对象,"
        "用报纸订阅来类比,报社一有新刊,所有订户自动收到。"
    )
    return body


# -------- 公共:合规的 A/B 输入 --------

_SKILL_A = (
    "# Concise\n\n"
    "## 核心原则\n"
    "1. 直接进入主题。\n"
    "2. 短句优先。\n"
    "3. 删掉冗词。\n\n"
    "## 行为约束\n"
    "- 禁止套话开头。\n\n"
    "## 示例\n示例略。"
)

_SKILL_B = (
    "# Detailed\n\n"
    "## 核心原则\n"
    "1. 充分铺垫背景。\n"
    "2. 每论点给一个具体例子。\n"
    "3. 多角度展开。\n\n"
    "## 行为约束\n"
    "- 禁止只给结论不给理由。\n\n"
    "## 示例\n示例略。"
)

_TASK_CTX = "通用写作任务,目标是清晰、有结构、不啰嗦"


# -------- 用例 1:空 judge_feedback 正常处理 --------

class TestFuseEmptyFeedback:
    def test_empty_feedback_string_does_not_break(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """judge_feedback 为空字符串时,fuse_skills 应仍能正常返回。"""
        fake = _FakeDeepSeekClient(response_text=_good_fused_skill())
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        result = fuse_skills(
            skill_a_content=_SKILL_A,
            skill_a_name="concise-writer",
            skill_b_content=_SKILL_B,
            skill_b_name="detailed-writer",
            task_context=_TASK_CTX,
            judge_feedback="",
        )

        assert result.startswith("# ")
        assert "## 核心原则" in result
        assert "## 行为约束" in result
        assert "## 示例" in result
        # 调了 1 次 execute
        assert len(fake.calls) == 1

    def test_none_feedback_equivalent_to_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """judge_feedback 为 None 时也应被处理为占位文本。"""
        fake = _FakeDeepSeekClient(response_text=_good_fused_skill())
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        result = fuse_skills(
            skill_a_content=_SKILL_A,
            skill_a_name="a",
            skill_b_content=_SKILL_B,
            skill_b_name="b",
            task_context=_TASK_CTX,
            judge_feedback=None,  # type: ignore[arg-type]
        )
        # 第一次调用的 user message 中应该有占位说明,而不是空
        user_msg = fake.calls[0][1]["content"]
        assert "(无评判反馈)" in user_msg
        assert result.startswith("# ")


# -------- 用例 2:输出长度在 150-400 字之间 --------

class TestFuseLength:
    def test_output_length_in_range(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常路径下,融合产物的非空白字符数应 ∈ [150, 400]。"""
        fake = _FakeDeepSeekClient(response_text=_good_fused_skill())
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        result = fuse_skills(
            skill_a_content=_SKILL_A,
            skill_a_name="a",
            skill_b_content=_SKILL_B,
            skill_b_name="b",
            task_context=_TASK_CTX,
            judge_feedback="A 强在简洁,B 强在例子",
        )

        compact = re.sub(r"\s+", "", result)
        assert FUSE_MIN_LENGTH <= len(compact) <= FUSE_MAX_LENGTH, (
            f"长度 {len(compact)} 超出区间 [{FUSE_MIN_LENGTH},{FUSE_MAX_LENGTH}]"
        )

    def test_overlength_output_is_truncated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """当模型返回过长但分布均匀时,fuse_skills 应按"非空白字符数"截断到 ≤ FUSE_MAX_LENGTH。

        注意:输入必须保证三个 H2 都在 FUSE_MAX_LENGTH 范围内可被容纳;
        若模型把字数全堆在一个章节上,_finalize 会主动抛 ValueError
        触发修复重试(见 TestFuseLengthBandEnforcement::test_overlength_drops_section_triggers_retry)。
        """
        # 三个章节均匀分布,每个 ~150 字符,合计 ~500,超 400
        huge_body = (
            "# Long\n\n"
            "## 核心原则\n" + ("P" * 150) + "\n\n"
            "## 行为约束\n" + ("Q" * 150) + "\n\n"
            "## 示例\n" + ("R" * 150)
        )
        assert len(re.sub(r"\s+", "", huge_body)) > FUSE_MAX_LENGTH  # 前提

        fake = _FakeDeepSeekClient(response_text=huge_body)
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        result = fuse_skills(
            skill_a_content=_SKILL_A,
            skill_a_name="a",
            skill_b_content=_SKILL_B,
            skill_b_name="b",
            task_context=_TASK_CTX,
            judge_feedback="",
        )

        compact = re.sub(r"\s+", "", result)
        assert len(compact) <= FUSE_MAX_LENGTH, (
            f"超长内容应被截断到 {FUSE_MAX_LENGTH} 字,实际 {len(compact)}"
        )
        # 截断后三个 H2 仍必须存在(因为输入分布均匀)
        assert "## 核心原则" in result
        assert "## 行为约束" in result
        assert "## 示例" in result
        # 且在 [150, 400] 区间内
        assert FUSE_MIN_LENGTH <= len(compact) <= FUSE_MAX_LENGTH


# -------- 用例 3:prompt 包含 A/B 核心差异 --------

class TestFusePrompt:
    def test_prompt_includes_both_skill_contents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """prompt 必须包含 A、B 全文 + 它们的核心差异/评判反馈。"""
        fake = _FakeDeepSeekClient(response_text=_good_fused_skill())
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        feedback_text = "A 在简洁度上 9 分,B 在例子丰富度上 9 分"
        fuse_skills(
            skill_a_content=_SKILL_A,
            skill_a_name="concise-writer",
            skill_b_content=_SKILL_B,
            skill_b_name="detailed-writer",
            task_context=_TASK_CTX,
            judge_feedback=feedback_text,
        )

        user_msg = fake.calls[0][1]["content"]
        # 模板内 A、B 内容
        assert "## Skill A (concise-writer)" in user_msg
        assert "## Skill B (detailed-writer)" in user_msg
        assert _SKILL_A.strip() in user_msg
        assert _SKILL_B.strip() in user_msg
        # 评判反馈原样塞入
        assert feedback_text in user_msg
        # 任务上下文
        assert _TASK_CTX in user_msg

    def test_prompt_template_constant_has_core_constraints(self) -> None:
        """FUSE_USER_PROMPT_TEMPLATE 应包含 A/B 强项保留与弱项回避的字样。"""
        template = FUSE_USER_PROMPT_TEMPLATE
        # 必须含有的关键约束
        assert "保留" in template  # 至少一处"保留"
        assert "避免" in template  # 至少一处"避免"
        assert "核心原则" in template
        assert "行为约束" in template
        assert "示例" in template
        # 长度约束
        assert "150" in template
        assert "400" in template

    def test_messages_structure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """messages 必须是 system + user 两条。"""
        fake = _FakeDeepSeekClient(response_text=_good_fused_skill())
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        fuse_skills(
            skill_a_content=_SKILL_A,
            skill_a_name="a",
            skill_b_content=_SKILL_B,
            skill_b_name="b",
            task_context=_TASK_CTX,
            judge_feedback="ok",
        )

        msgs = fake.calls[0]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        # system 提示词应明确"融合"
        assert "融合" in msgs[0]["content"]


# -------- 用例 4:失败重试与异常路径 --------

class TestFuseFailure:
    def test_first_invalid_triggers_retry_then_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """第一次返回缺 H1 标题的坏产物,第二次返回合格产物 → 应成功。"""
        bad_then_good = iter(
            [
                "不是 markdown 也不是 skill",
                _good_fused_skill(name="Retried"),
            ]
        )
        fake = _FakeDeepSeekClient(response_text="placeholder")
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        def dynamic_execute(
            messages: list[dict[str, str]],
            *,
            model: str | None = None,
            temperature: float = 0.7,
        ) -> CompletionResult:
            fake.calls.append(list(messages))
            text = next(bad_then_good)
            return CompletionResult(
                content=text,
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
                model="fake",
            )

        fake.execute = dynamic_execute  # type: ignore[assignment]

        result = fuse_skills(
            skill_a_content=_SKILL_A,
            skill_a_name="a",
            skill_b_content=_SKILL_B,
            skill_b_name="b",
            task_context=_TASK_CTX,
            judge_feedback="",
        )
        assert result.startswith("# Retried")
        # 共调 2 次
        assert len(fake.calls) == 2
        # 第二次 messages 末尾应包含"修复 prompt"指令
        assert "重新生成" in fake.calls[1][-1]["content"] or "严格" in fake.calls[1][-1]["content"]

    def test_two_failures_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """两次都返回坏产物 → 抛 RuntimeError 带上下文。"""
        fake = _FakeDeepSeekClient(response_text="this is not a skill")
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        with pytest.raises(RuntimeError) as exc_info:
            fuse_skills(
                skill_a_content=_SKILL_A,
                skill_a_name="a",
                skill_b_content=_SKILL_B,
                skill_b_name="b",
                task_context=_TASK_CTX,
                judge_feedback="",
            )
        msg = str(exc_info.value)
        assert "两次解析均失败" in msg

    def test_empty_input_raises_value_error(self) -> None:
        """空 skill_content / task_context 应在客户端被调用前就抛 ValueError。"""
        with pytest.raises(ValueError):
            fuse_skills(
                skill_a_content="",
                skill_a_name="a",
                skill_b_content=_SKILL_B,
                skill_b_name="b",
                task_context=_TASK_CTX,
                judge_feedback="",
            )
        with pytest.raises(ValueError):
            fuse_skills(
                skill_a_content=_SKILL_A,
                skill_a_name="a",
                skill_b_content="",
                skill_b_name="b",
                task_context=_TASK_CTX,
                judge_feedback="",
            )
        with pytest.raises(ValueError):
            fuse_skills(
                skill_a_content=_SKILL_A,
                skill_a_name="a",
                skill_b_content=_SKILL_B,
                skill_b_name="b",
                task_context="",
                judge_feedback="",
            )


# -------- 用例 5:length band 硬约束(verifier 反馈回归保护)--------

class TestFuseLengthBandEnforcement:
    """锁定 _finalize 对 [FUSE_MIN_LENGTH, FUSE_MAX_LENGTH] 硬约束的修复。

    三个核心约束:
    1. 偏短(<150) → 视为不合规,触发 fuse_skills 内的修复重试。
    2. 偏长(>400) → 按"非空白字符数"截断到 400(不是按原始字符数)。
    3. 偏长且大量空白 → 截断后 compact_len 应恰好是 400(修复前的 bug 是
       按原始 body[:400] 切片,会留下大量空字符,实际 compact_len 远小于 400)。
    """

    def test_too_short_triggers_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """模型返回 < 150 字的产物时,fuse_skills 必须触发修复重试。

        流程:第一次返回 50 字 → _finalize 抛 ValueError → fuse_skills
        追加修复 prompt 重试 → 第二次返回合规 318 字 → 成功返回。
        """
        too_short = (
            "# T\n\n## 核心原则\n1. 太短了。\n\n"
            "## 行为约束\n- 禁止。\n\n## 示例\n略。"
        )
        # 确认前提:确实 < FUSE_MIN_LENGTH
        assert len(re.sub(r"\s+", "", too_short)) < FUSE_MIN_LENGTH

        good = _good_fused_skill(name="Recovered")
        # 预置两次返回:坏、好
        responses = iter([too_short, good])
        fake = _FakeDeepSeekClient(response_text="placeholder")
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        def dynamic_execute(
            messages: list[dict[str, str]],
            *,
            model: str | None = None,
            temperature: float = 0.7,
        ) -> CompletionResult:
            fake.calls.append(list(messages))
            text = next(responses)
            return CompletionResult(
                content=text,
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
                model="fake",
            )

        fake.execute = dynamic_execute  # type: ignore[assignment]

        result = fuse_skills(
            skill_a_content=_SKILL_A,
            skill_a_name="a",
            skill_b_content=_SKILL_B,
            skill_b_name="b",
            task_context=_TASK_CTX,
            judge_feedback="",
        )
        # 重试后应成功
        assert result.startswith("# Recovered")
        # 关键:确实发生了 2 次调用(1 次失败 → 1 次成功)
        assert len(fake.calls) == 2
        # 第二次调用必须含修复 prompt
        assert "重新生成" in fake.calls[1][-1]["content"]

    def test_too_short_twice_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """两次都偏短 → 抛 RuntimeError(两次解析均失败)。"""
        too_short = "# T\n\n## 核心原则\n1. x\n\n## 行为约束\n- y\n\n## 示例\n略"
        # 前提:确认 < 150
        assert len(re.sub(r"\s+", "", too_short)) < FUSE_MIN_LENGTH

        fake = _FakeDeepSeekClient(response_text=too_short)
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        with pytest.raises(RuntimeError) as exc_info:
            fuse_skills(
                skill_a_content=_SKILL_A,
                skill_a_name="a",
                skill_b_content=_SKILL_B,
                skill_b_name="b",
                task_context=_TASK_CTX,
                judge_feedback="",
            )
        assert "两次解析均失败" in str(exc_info.value)

    def test_overlength_with_whitespace_truncates_by_compact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """超长产物按"非空白字符数"截断到 ≤ 400(不是按原始字符数)。

        关键修复点:原 bug 是 body[:FUSE_MAX_LENGTH],会把
        'X' + ' ' * 800 这样的输入截成 400 个 raw 字符,实际
        compact_len 只有 ~200。修复后按 compact 截断,确保
        compact_len ∈ [150, 400](且必需要节不丢失)。

        这里构造的输入三个章节均匀分布,每个 ~150 字符,合计 ~471。
        所有 H2 marker 都在 compact offset < 400 内。
        """
        # 构造:核心原则 / 行为约束 / 示例 各 150 字符(穿插空格)
        # 总 compact ~471 > 400
        huge_body = (
            "# Long\n\n"
            "## 核心原则\n" + ("P " * 150) + "\n\n"
            "## 行为约束\n" + ("Q " * 150) + "\n\n"
            "## 示例\n" + ("R " * 150)
        )
        compact_in = re.sub(r"\s+", "", huge_body)
        assert len(compact_in) > FUSE_MAX_LENGTH, (
            f"前提:必须 > 400,实际 {len(compact_in)}"
        )

        fake = _FakeDeepSeekClient(response_text=huge_body)
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        result = fuse_skills(
            skill_a_content=_SKILL_A,
            skill_a_name="a",
            skill_b_content=_SKILL_B,
            skill_b_name="b",
            task_context=_TASK_CTX,
            judge_feedback="",
        )

        compact_out = re.sub(r"\s+", "", result)
        # 修复后:compact_len ∈ [150, 400](不再 > 400)
        assert FUSE_MIN_LENGTH <= len(compact_out) <= FUSE_MAX_LENGTH, (
            f"修复后期望 compact_len ∈ [{FUSE_MIN_LENGTH}, {FUSE_MAX_LENGTH}],"
            f" 实际 {len(compact_out)}"
        )
        # 三个 H2 都还在(本测试构造允许)
        assert "## 核心原则" in result
        assert "## 行为约束" in result
        assert "## 示例" in result

    def test_overlength_drops_section_triggers_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """若 400 字截断点会丢掉必需要节,应触发修复重试。"""
        # 构造:核心原则一个就占满 400 字,后两个章节放最后
        # 修复后:截到 400 时只到核心原则末尾,行为约束/示例丢失 → 重试
        bloated = (
            "# X\n\n"
            "## 核心原则\n" + ("P" * 500) + "\n\n"
            "## 行为约束\n- 禁止 X。\n\n"
            "## 示例\n示例"
        )
        # 前提:确实 > 400
        assert len(re.sub(r"\s+", "", bloated)) > FUSE_MAX_LENGTH

        good = _good_fused_skill(name="Recovered")
        responses = iter([bloated, good])
        fake = _FakeDeepSeekClient(response_text="placeholder")
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        def dynamic_execute(
            messages: list[dict[str, str]],
            *,
            model: str | None = None,
            temperature: float = 0.7,
        ) -> CompletionResult:
            fake.calls.append(list(messages))
            text = next(responses)
            return CompletionResult(
                content=text,
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
                model="fake",
            )

        fake.execute = dynamic_execute  # type: ignore[assignment]

        result = fuse_skills(
            skill_a_content=_SKILL_A,
            skill_a_name="a",
            skill_b_content=_SKILL_B,
            skill_b_name="b",
            task_context=_TASK_CTX,
            judge_feedback="",
        )
        # 重试后应成功
        assert result.startswith("# Recovered")
        # 关键:确实发生了 2 次调用(1 次截断失败 → 1 次成功)
        assert len(fake.calls) == 2

    def test_truncation_keeps_required_sections_when_balanced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """三个 H2 均匀分布的超长输入,截断后三个 H2 仍必须存在。"""
        # 三个章节大致均匀,每个 ~150 compact chars
        huge = (
            "# Long\n\n"
            "## 核心原则\n" + ("P" * 150) + "\n\n"
            "## 行为约束\n" + ("Q" * 150) + "\n\n"
            "## 示例\n" + ("R" * 150)
        )
        assert len(re.sub(r"\s+", "", huge)) > FUSE_MAX_LENGTH  # 前提

        fake = _FakeDeepSeekClient(response_text=huge)
        monkeypatch.setattr("arena.fuse.DeepSeekClient", lambda: fake)

        result = fuse_skills(
            skill_a_content=_SKILL_A,
            skill_a_name="a",
            skill_b_content=_SKILL_B,
            skill_b_name="b",
            task_context=_TASK_CTX,
            judge_feedback="",
        )

        # 三个必需 H2 必须都在(因为分布均匀,截到 400 不会丢)
        assert "## 核心原则" in result
        assert "## 行为约束" in result
        assert "## 示例" in result
        # 长度合规
        compact = re.sub(r"\s+", "", result)
        assert FUSE_MIN_LENGTH <= len(compact) <= FUSE_MAX_LENGTH

    def test_direct_finalize_short_raises(self) -> None:
        """直接调 _finalize 验证短产物抛 ValueError(不依赖 retry 路径)。"""
        from arena.fuse import _finalize

        too_short = (
            "# T\n\n## 核心原则\n1. x\n\n## 行为约束\n- y\n\n## 示例\n略"
        )
        with pytest.raises(ValueError) as exc_info:
            _finalize(too_short, context={"test": True})
        # 错误信息应明确提到"偏短"
        assert "偏短" in str(exc_info.value)

    def test_direct_finalize_at_min_length_passes(self) -> None:
        """直接调 _finalize,正好 FUSE_MIN_LENGTH 字(且结构齐全)的产物应通过。"""
        from arena.fuse import _finalize, FUSE_MIN_LENGTH

        # 构造一个结构齐全、compact_len == FUSE_MIN_LENGTH 的产物
        # 标题"H1" + "核心原则"5字 + 行为约束5字 + 示例3字 = 16 字固定开销
        # 主体填充需要补足 FUSE_MIN_LENGTH
        overhead_chars = (
            len("T")  # 标题
            + len("核心原则")  # 5
            + len("行为约束")  # 4
            + len("示例")  # 2
        )
        filler_len = FUSE_MIN_LENGTH - overhead_chars
        # 在核心原则中放 filler_len 个 X,行为约束写一句、示例写一句
        body = (
            f"# T\n\n"
            f"## 核心原则\n{filler_len * 'X'}\n\n"
            f"## 行为约束\n- 禁止 X。\n\n"
            f"## 示例\n示例示例"
        )
        # 校准:确认 compact_len 命中 FUSE_MIN_LENGTH
        compact = re.sub(r"\s+", "", body)
        if len(compact) != FUSE_MIN_LENGTH:
            # 校准:加 / 减 X 以精确到 FUSE_MIN_LENGTH
            diff = FUSE_MIN_LENGTH - len(compact)
            if diff > 0:
                body = body.replace("X" * filler_len, "X" * (filler_len + diff), 1)
            else:
                body = body.replace("X" * filler_len, "X" * (filler_len + diff), 1)

        compact_check = re.sub(r"\s+", "", body)
        assert len(compact_check) == FUSE_MIN_LENGTH, (
            f"校准失败:actual={len(compact_check)} 期望={FUSE_MIN_LENGTH}"
        )

        result = _finalize(body, context={"test": True})
        compact_out = re.sub(r"\s+", "", result)
        assert FUSE_MIN_LENGTH <= len(compact_out) <= FUSE_MAX_LENGTH
