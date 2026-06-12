"""judge 模块单测:prompt 构造 + JSON 抽取 + pydantic 校验 + compare 端到端(全 mock)。"""
from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from arena.deepseek_client import CompletionResult
from arena.judge import (
    JUDGE_DIMENSIONS,
    DimensionScores,
    Verdict,
    _extract_json,
    build_judge_messages,
    compare,
)


# -------- Verdict pydantic schema --------

class TestVerdictSchema:
    def _valid_data(self) -> dict[str, Any]:
        return {
            "winner": "A",
            "scores": {
                "A": {"correctness": 8, "completeness": 7, "clarity": 9, "creativity": 6},
                "B": {"correctness": 6, "completeness": 7, "clarity": 8, "creativity": 7},
            },
            "reasoning": "A 更准确",
        }

    def test_valid_verdict(self) -> None:
        v = Verdict.model_validate(self._valid_data())
        assert v.winner == "A"
        assert v.total_score("A") == 8 + 7 + 9 + 6
        assert v.total_score("B") == 6 + 7 + 8 + 7

    def test_lowercase_winner_normalized(self) -> None:
        data = self._valid_data()
        data["winner"] = "b"
        v = Verdict.model_validate(data)
        assert v.winner == "B"

    def test_tie_normalized(self) -> None:
        data = self._valid_data()
        data["winner"] = "draw"
        v = Verdict.model_validate(data)
        assert v.winner == "tie"

    def test_to_score(self) -> None:
        a = Verdict.model_validate(self._valid_data())
        assert a.to_score() == 1.0

        data = self._valid_data()
        data["winner"] = "B"
        b = Verdict.model_validate(data)
        assert b.to_score() == 0.0

        data["winner"] = "tie"
        t = Verdict.model_validate(data)
        assert t.to_score() == 0.5

    def test_score_out_of_range_rejected(self) -> None:
        data = self._valid_data()
        data["scores"]["A"]["correctness"] = 11
        with pytest.raises(ValidationError):
            Verdict.model_validate(data)

    def test_negative_score_rejected(self) -> None:
        data = self._valid_data()
        data["scores"]["A"]["clarity"] = -1
        with pytest.raises(ValidationError):
            Verdict.model_validate(data)

    def test_missing_side_rejected(self) -> None:
        data = self._valid_data()
        del data["scores"]["B"]
        with pytest.raises(ValidationError):
            Verdict.model_validate(data)

    def test_invalid_winner_rejected(self) -> None:
        data = self._valid_data()
        data["winner"] = "C"
        with pytest.raises(ValidationError):
            Verdict.model_validate(data)

    def test_total_score_invalid_side_raises(self) -> None:
        v = Verdict.model_validate(self._valid_data())
        with pytest.raises(KeyError):
            v.total_score("C")

    def test_dimension_scores_independent(self) -> None:
        ds = DimensionScores(
            correctness=5, completeness=5, clarity=5, creativity=5
        )
        assert ds.correctness == 5
        # 字段独立:修改 correctness 不应影响 clarity
        # (pydantic BaseModel 默认是不可变 model,但我们要的是"字段独立")
        ds2 = DimensionScores(
            correctness=10, completeness=0, clarity=0, creativity=0
        )
        assert ds2.correctness == 10
        assert ds2.clarity == 0


# -------- build_judge_messages --------

class TestBuildJudgeMessages:
    def test_basic_messages_structure(self) -> None:
        msgs = build_judge_messages(
            task="写一句诗",
            output_a="床前明月光",
            output_b="疑是地上霜",
        )
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert "judge" in msgs[0]["content"].lower() or "裁判" in msgs[0]["content"]
        assert msgs[1]["role"] == "user"
        # user 消息必须包含任务 + A + B + 匿名标签
        user_msg = msgs[1]["content"]
        assert "写一句诗" in user_msg
        assert "Response A" in user_msg
        assert "Response B" in user_msg
        assert "床前明月光" in user_msg
        assert "疑是地上霜" in user_msg

    def test_dimensions_listed_in_system_prompt(self) -> None:
        msgs = build_judge_messages("t", "a", "b")
        system_msg = msgs[0]["content"]
        for dim in JUDGE_DIMENSIONS:
            assert dim in system_msg, f"系统提示缺少维度: {dim}"

    def test_skill_metadata_only_when_provided(self) -> None:
        msgs_no_meta = build_judge_messages("t", "a", "b")
        assert "仅供审计" not in msgs_no_meta[1]["content"]

        msgs_with_meta = build_judge_messages(
            "t", "a", "b", skill_a="concise-writer", skill_b="detailed-writer"
        )
        assert "concise-writer" in msgs_with_meta[1]["content"]
        assert "detailed-writer" in msgs_with_meta[1]["content"]
        assert "仅供审计" in msgs_with_meta[1]["content"]


# -------- _extract_json --------

class TestExtractJson:
    def test_pure_json(self) -> None:
        text = '{"winner": "A", "scores": {}}'
        assert _extract_json(text) == {"winner": "A", "scores": {}}

    def test_markdown_fenced(self) -> None:
        text = '```json\n{"winner": "B", "scores": {}}\n```'
        assert _extract_json(text) == {"winner": "B", "scores": {}}

    def test_markdown_fenced_no_lang(self) -> None:
        text = '```\n{"winner": "tie", "scores": {}}\n```'
        assert _extract_json(text) == {"winner": "tie", "scores": {}}

    def test_surrounded_by_text(self) -> None:
        text = '当然,我的判定如下:\n{"winner": "A", "scores": {}}\n完毕。'
        assert _extract_json(text) == {"winner": "A", "scores": {}}

    def test_invalid_text_raises(self) -> None:
        with pytest.raises(ValueError):
            _extract_json("not json at all")

    def test_partial_json_raises(self) -> None:
        # 没有完整的大括号闭合
        with pytest.raises(ValueError):
            _extract_json('{"winner": "A", "scores":')


# -------- compare 端到端(全 mock) --------

class TestCompareEndToEnd:
    @pytest.fixture
    def fake_judge_client(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        """构造一个假 DeepSeekClient,其 judge 方法返回预设的 JSON。"""

        class Fake:
            def __init__(self, response_text: str) -> None:
                self.response_text = response_text
                self.calls: list[list[dict[str, str]]] = []

            def judge(
                self,
                messages: list[dict[str, str]],
                *,
                model: str | None = None,
                temperature: float = 0.2,
            ) -> CompletionResult:
                self.calls.append(list(messages))
                return CompletionResult(
                    content=self.response_text,
                    prompt_tokens=20,
                    completion_tokens=10,
                    total_tokens=30,
                    model=model or "fake-judge",
                )

            def execute(self, *args: Any, **kwargs: Any) -> Any:
                raise NotImplementedError

        return Fake

    def test_happy_path(self, monkeypatch: pytest.MonkeyPatch, fake_judge_client: Any) -> None:
        response = json.dumps(
            {
                "winner": "A",
                "scores": {
                    "A": {"correctness": 9, "completeness": 8, "clarity": 9, "creativity": 7},
                    "B": {"correctness": 7, "completeness": 7, "clarity": 8, "creativity": 8},
                },
                "reasoning": "A 更直接",
            },
            ensure_ascii=False,
        )
        fake = fake_judge_client(response_text=response)
        monkeypatch.setattr("arena.judge.DeepSeekClient", lambda: fake)

        verdict = compare(
            task="写一句话",
            output_a="简洁版本",
            output_b="啰嗦版本",
            skill_a="concise-writer",
            skill_b="detailed-writer",
        )

        assert verdict.winner == "A"
        assert verdict.total_score("A") == 9 + 8 + 9 + 7
        assert verdict.total_score("B") == 7 + 7 + 8 + 8
        assert verdict.to_score() == 1.0
        # 调用应当发生了 1 次
        assert len(fake.calls) == 1

    def test_markdown_fenced_response_parses(
        self, monkeypatch: pytest.MonkeyPatch, fake_judge_client: Any
    ) -> None:
        response = (
            '```json\n'
            '{"winner":"tie","scores":{"A":{"correctness":7,"completeness":7,'
            '"clarity":7,"creativity":7},"B":{"correctness":7,"completeness":7,'
            '"clarity":7,"creativity":7}},"reasoning":"平局"}\n'
            '```'
        )
        fake = fake_judge_client(response_text=response)
        monkeypatch.setattr("arena.judge.DeepSeekClient", lambda: fake)

        verdict = compare(task="t", output_a="a", output_b="b")
        assert verdict.winner == "tie"
        assert verdict.to_score() == 0.5

    def test_invalid_first_response_triggers_retry(
        self, monkeypatch: pytest.MonkeyPatch, fake_judge_client: Any
    ) -> None:
        # 第一次返回坏 JSON,第二次返回好 JSON → 应该走修复路径并成功
        responses = iter(
            [
                "this is not json at all",
                json.dumps(
                    {
                        "winner": "B",
                        "scores": {
                            "A": {"correctness": 5, "completeness": 5,
                                  "clarity": 5, "creativity": 5},
                            "B": {"correctness": 9, "completeness": 9,
                                  "clarity": 9, "creativity": 9},
                        },
                        "reasoning": "B 更好",
                    }
                ),
            ]
        )
        fake = fake_judge_client(response_text="placeholder")

        def dynamic_judge(
            messages: list[dict[str, str]],
            *,
            model: str | None = None,
            temperature: float = 0.2,
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

        fake.judge = dynamic_judge  # type: ignore[assignment]
        monkeypatch.setattr("arena.judge.DeepSeekClient", lambda: fake)

        verdict = compare(task="t", output_a="a", output_b="b")
        assert verdict.winner == "B"
        # 第一次失败 → 第二次修复,共 2 次调用
        assert len(fake.calls) == 2
        # 第二次 messages 应当包含"修复 prompt"指令
        assert "JSON" in fake.calls[1][-1]["content"]

    def test_two_failures_raise(
        self, monkeypatch: pytest.MonkeyPatch, fake_judge_client: Any
    ) -> None:
        fake = fake_judge_client(response_text="still not json")
        monkeypatch.setattr("arena.judge.DeepSeekClient", lambda: fake)

        with pytest.raises(RuntimeError) as exc_info:
            compare(task="t", output_a="a", output_b="b")
        assert "Verdict 解析两次均失败" in str(exc_info.value)

    def test_messages_passed_to_judge(
        self, monkeypatch: pytest.MonkeyPatch, fake_judge_client: Any
    ) -> None:
        response = json.dumps(
            {
                "winner": "A",
                "scores": {
                    "A": {"correctness": 5, "completeness": 5, "clarity": 5, "creativity": 5},
                    "B": {"correctness": 5, "completeness": 5, "clarity": 5, "creativity": 5},
                },
                "reasoning": "ok",
            }
        )
        fake = fake_judge_client(response_text=response)
        monkeypatch.setattr("arena.judge.DeepSeekClient", lambda: fake)

        compare(
            task="写诗",
            output_a="床前明月光",
            output_b="疑是地上霜",
        )
        msgs = fake.calls[0]
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        # 关键内容必须出现在 user message
        user = msgs[1]["content"]
        assert "写诗" in user
        assert "床前明月光" in user
        assert "疑是地上霜" in user
        # 匿名化标签
        assert "Response A" in user
        assert "Response B" in user