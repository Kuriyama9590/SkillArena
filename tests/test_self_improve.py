"""self_improve 模块单测:空 weaknesses 短路 / 端到端改进 / 循环控制(全 mock)。"""
from __future__ import annotations

from typing import Any

import pytest

from arena.deepseek_client import CompletionResult
from arena.self_improve import (
    improve_skill,
    run_improvement_cycle,
)


# -------- 公共假 client --------

class _FakeDeepSeekClient:
    """可记录调用、预设返回的 DeepSeekClient 替身。"""

    def __init__(self, response_text: str | None = None) -> None:
        self.response_text = response_text or ""
        self.calls: list[list[dict[str, str]]] = []
        self._queue: list[str] = []
        if response_text is not None:
            self._queue.append(response_text)

    def execute(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> CompletionResult:
        self.calls.append(list(messages))
        if self._queue:
            text = self._queue.pop(0)
        else:
            text = self.response_text
        return CompletionResult(
            content=text,
            prompt_tokens=20,
            completion_tokens=80,
            total_tokens=100,
            model=model or "fake-execute",
        )

    def judge(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> CompletionResult:
        raise NotImplementedError("self_improve 不调用 judge")

    @property
    def settings(self) -> Any:
        class _S:
            execute_model = "fake-execute"

        return _S()


# -------- 公共:输入样本 --------

_SKILL = (
    "# Foo\n\n"
    "## 核心原则\n"
    "1. 旧原则 A。\n"
    "2. 旧原则 B。\n\n"
    "## 行为约束\n"
    "- 旧约束。\n\n"
    "## 示例\n旧示例。"
)


def _improved_skill(name: str = "Foo") -> str:
    """构造一份合规的改进产物。"""
    return (
        f"# {name}\n\n"
        "## 核心原则\n"
        "1. 改进原则 A:针对 weakness 1。\n"
        "2. 改进原则 B:针对 weakness 2。\n"
        "3. 保留旧原则。\n\n"
        "## 行为约束\n"
        "- 禁止 X。\n"
        "- 禁止 Y。\n"
    )


# -------- 用例 1:空 weaknesses 直接返回原 skill --------

class TestEmptyWeaknesses:
    def test_empty_list_returns_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """weaknesses=[] 时,improve_skill 应直接返回原 skill,不发 API 请求。"""
        fake = _FakeDeepSeekClient()
        # 即使我们"误注入了" client,也不应被调用
        # 但 monkeypatch 的 _FakeDeepSeekClient() 会在 _ensure_client 中被构造
        monkeypatch.setattr(
            "arena.self_improve.DeepSeekClient", lambda: fake
        )
        # 不预置任何返回;若 execute 被调用,会 raise NotImplementedError

        out = improve_skill(
            skill_content=_SKILL, skill_name="foo", weaknesses=[]
        )
        assert out == _SKILL
        assert len(fake.calls) == 0  # 关键:根本没调 API

    def test_none_or_whitespace_weaknesses_treated_as_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """只含空白字符串的 weaknesses 也应走短路。"""
        fake = _FakeDeepSeekClient()
        monkeypatch.setattr(
            "arena.self_improve.DeepSeekClient", lambda: fake
        )

        out = improve_skill(
            skill_content=_SKILL,
            skill_name="foo",
            weaknesses=["", "   ", "\n\t"],
        )
        assert out == _SKILL
        assert len(fake.calls) == 0


# -------- 用例 2:正常改进路径 --------

class TestImproveNormalPath:
    def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """weaknesses 非空时,improve_skill 应调一次 API 并返回新 skill。"""
        fake = _FakeDeepSeekClient(response_text=_improved_skill())
        monkeypatch.setattr(
            "arena.self_improve.DeepSeekClient", lambda: fake
        )

        out = improve_skill(
            skill_content=_SKILL,
            skill_name="foo",
            weaknesses=["缺例子", "结构不清"],
        )
        assert out.startswith("# Foo")
        assert "## 核心原则" in out
        assert "## 行为约束" in out
        assert len(fake.calls) == 1
        # prompt 应包含 weaknesses
        user_msg = fake.calls[0][1]["content"]
        assert "缺例子" in user_msg
        assert "结构不清" in user_msg

    def test_first_invalid_triggers_retry_then_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """第一次返回不合规 skill,第二次返回合规 → 走修复 prompt 后成功。"""
        good = _improved_skill(name="Foo2")
        queue = iter(["not a skill", good])
        fake = _FakeDeepSeekClient()
        monkeypatch.setattr(
            "arena.self_improve.DeepSeekClient", lambda: fake
        )

        def dynamic(
            messages: list[dict[str, str]],
            *,
            model: str | None = None,
            temperature: float = 0.7,
        ) -> CompletionResult:
            fake.calls.append(list(messages))
            text = next(queue)
            return CompletionResult(
                content=text,
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
                model="fake",
            )

        fake.execute = dynamic  # type: ignore[assignment]

        out = improve_skill(
            skill_content=_SKILL,
            skill_name="foo",
            weaknesses=["缺例子"],
        )
        assert out.startswith("# Foo2")
        assert len(fake.calls) == 2
        # 第二次 messages 末尾应包含修复 prompt
        assert "重新生成" in fake.calls[1][-1]["content"]

    def test_two_failures_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """两次都失败 → 抛 RuntimeError。"""
        fake = _FakeDeepSeekClient(response_text="garbage")
        monkeypatch.setattr(
            "arena.self_improve.DeepSeekClient", lambda: fake
        )
        with pytest.raises(RuntimeError) as exc_info:
            improve_skill(
                skill_content=_SKILL,
                skill_name="foo",
                weaknesses=["缺例子"],
            )
        assert "两次解析均失败" in str(exc_info.value)


# -------- 用例 3:run_improvement_cycle —— 提前终止 / max_iterations / 空 weaknesses 退出 --------

class TestRunImprovementCycle:
    def test_elo_target_met_terminates_early(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """当 Elo 提升达标时,循环应提前结束。"""
        # 假 client:每轮都返回合规新 skill
        fake = _FakeDeepSeekClient(response_text=_improved_skill(name="Foo"))
        monkeypatch.setattr(
            "arena.self_improve.DeepSeekClient", lambda: fake
        )

        # 假 evaluator:每次调用 Elo +30(目标 20),永远有 1 个 weakness
        call_count = {"n": 0}

        def fake_evaluator(
            skill_content: str, skill_name: str
        ) -> tuple[float, list[str]]:
            call_count["n"] += 1
            return (1500.0 + 30 * call_count["n"], ["缺例子"])

        report = run_improvement_cycle(
            skill_name="foo",
            skill_content=_SKILL,
            max_iterations=5,
            target_elo_delta=20.0,
            evaluator=fake_evaluator,
        )

        # 1 轮就达到 target,应只跑 1 步
        assert report.converged is True
        assert report.total_iterations == 1
        assert len(report.steps) == 1
        # 关键:单轮 Elo 提升 >= 20
        assert report.steps[0].elo_delta >= 20.0
        # "达成目标" 应出现在 notes
        assert "达成目标" in report.notes
        # final_elo 应当是本轮 post-eval 之后的分数
        assert report.final_elo == report.steps[-1].elo_after

    def test_max_iterations_forces_termination(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """当 Elo 提升永远不达标时,应在 max_iterations 后强制停止。"""
        fake = _FakeDeepSeekClient(response_text=_improved_skill(name="Foo"))
        monkeypatch.setattr(
            "arena.self_improve.DeepSeekClient", lambda: fake
        )

        # 假 evaluator:每轮 Elo 提升只有 2 分,远低于 20
        call_count = {"n": 0}

        def fake_evaluator(
            skill_content: str, skill_name: str
        ) -> tuple[float, list[str]]:
            call_count["n"] += 1
            return (1500.0 + 2 * call_count["n"], ["缺例子"])

        report = run_improvement_cycle(
            skill_name="foo",
            skill_content=_SKILL,
            max_iterations=3,
            target_elo_delta=20.0,
            evaluator=fake_evaluator,
        )

        assert report.converged is False
        assert report.total_iterations == 3
        assert len(report.steps) == 3
        # notes 应说明达到 max_iterations
        assert "max_iterations=3" in report.notes

    def test_no_weaknesses_terminates_immediately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """evaluator 返回空 weaknesses 时,循环应立即收敛(0 步)。"""
        fake = _FakeDeepSeekClient(response_text=_improved_skill(name="Foo"))
        monkeypatch.setattr(
            "arena.self_improve.DeepSeekClient", lambda: fake
        )

        def always_good_evaluator(
            skill_content: str, skill_name: str
        ) -> tuple[float, list[str]]:
            return (1500.0, [])

        report = run_improvement_cycle(
            skill_name="foo",
            skill_content=_SKILL,
            max_iterations=5,
            target_elo_delta=20.0,
            evaluator=always_good_evaluator,
        )

        assert report.converged is True
        assert report.total_iterations == 0
        assert len(report.steps) == 0
        # 没调过 API
        assert len(fake.calls) == 0
        # notes 包含"无 weaknesses"
        assert "无 weaknesses" in report.notes

    def test_invalid_max_iterations_raises(self) -> None:
        """max_iterations < 1 应抛 ValueError。"""
        with pytest.raises(ValueError):
            run_improvement_cycle(
                skill_name="foo",
                skill_content=_SKILL,
                max_iterations=0,
            )
