"""runner 模块单测:skill 加载 + 执行调用(全部用 monkeypatch mock,不打真实网络)。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from arena.deepseek_client import CompletionResult
from arena.runner import (
    RunOutput,
    load_skill,
    load_skill_by_name,
    list_available_skills,
    run_with_skill,
)


# -------- Fixtures --------

@pytest.fixture
def skill_file(tmp_path: Path) -> Path:
    """创建一个临时 skill 文件。"""
    p = tmp_path / "test-skill.md"
    p.write_text(
        "# Test Skill\n\nAlways answer with one sentence starting with 'Answer:'.",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """构造一个假的 DeepSeekClient:替换 execute,返回固定 CompletionResult。

    返回的实例是 FakeClient,业务侧只调用 .execute(messages, ...)。
    """

    class FakeClient:
        def __init__(self, content: str = "mocked output") -> None:
            self.content = content
            self.calls: list[dict[str, Any]] = []

        def execute(
            self,
            messages: list[dict[str, str]],
            *,
            model: str | None = None,
            temperature: float = 0.7,
        ) -> CompletionResult:
            self.calls.append(
                {
                    "messages": list(messages),
                    "model": model,
                    "temperature": temperature,
                }
            )
            return CompletionResult(
                content=self.content,
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                model=model or "fake-model",
            )

        # runner 不会调用 judge,但为了类型完整性留一个
        def judge(self, *args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError

    fake = FakeClient(content="Answer: 42")
    # patch DeepSeekClient 构造,使其返回 fake
    monkeypatch.setattr("arena.runner.DeepSeekClient", lambda: fake)
    return fake


# -------- load_skill --------

class TestLoadSkill:
    def test_reads_full_content(self, skill_file: Path) -> None:
        content = load_skill(skill_file)
        assert "Test Skill" in content
        assert "Always answer with one sentence" in content

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_skill(tmp_path / "nope.md")

    def test_directory_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            load_skill(tmp_path)

    def test_string_path_accepted(self, skill_file: Path) -> None:
        # str 路径也要能工作
        content = load_skill(str(skill_file))
        assert content


# -------- load_skill_by_name --------

class TestLoadSkillByName:
    def test_loads_built_in_skill(self) -> None:
        # 内置 3 个 skill 之一
        content = load_skill_by_name("concise-writer")
        assert "Concise Writer" in content or "简洁" in content

    def test_missing_skill_raises_with_list(self) -> None:
        with pytest.raises(FileNotFoundError) as exc_info:
            load_skill_by_name("does-not-exist")
        # 错误消息里应当包含可用 skill 列表
        msg = str(exc_info.value)
        assert "concise-writer" in msg or "可用 skill" in msg


# -------- list_available_skills --------

class TestListAvailableSkills:
    def test_lists_builtin_skills(self) -> None:
        names = list_available_skills()
        assert "concise-writer" in names
        assert "detailed-writer" in names
        assert "structured-writer" in names

    def test_returns_sorted(self) -> None:
        names = list_available_skills()
        assert names == sorted(names)

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert list_available_skills(tmp_path) == []


# -------- run_with_skill --------

class TestRunWithSkill:
    def test_skill_injected_as_system_message(
        self, skill_file: Path, fake_client: Any
    ) -> None:
        skill_content = load_skill(skill_file)
        output = run_with_skill(
            task="What is 6*7?",
            skill_content=skill_content,
            skill_name="test-skill",
            client=fake_client,  # type: ignore[arg-type]
        )

        # fake_client.calls[0]["messages"] 应当有 system + user 两条
        msgs = fake_client.calls[0]["messages"]
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert "Always answer with one sentence" in msgs[0]["content"]
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "What is 6*7?"

        assert output.content == "Answer: 42"
        assert output.tokens == 15
        assert output.skill_name == "test-skill"
        assert output.task == "What is 6*7?"

    def test_no_skill_runs_baseline(self, fake_client: Any) -> None:
        output = run_with_skill(
            task="hello",
            skill_content=None,
            client=fake_client,  # type: ignore[arg-type]
        )
        msgs = fake_client.calls[0]["messages"]
        # 没有 system message,只有 user
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert output.skill_name is None

    def test_empty_skill_treated_as_no_skill(self, fake_client: Any) -> None:
        # 空字符串或纯空白视为没有 skill
        output = run_with_skill(
            task="hello",
            skill_content="   \n  ",
            client=fake_client,  # type: ignore[arg-type]
        )
        msgs = fake_client.calls[0]["messages"]
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert output.skill_name is None

    def test_empty_task_raises(self, fake_client: Any) -> None:
        with pytest.raises(ValueError):
            run_with_skill(task="", skill_content="x", client=fake_client)  # type: ignore[arg-type]

    def test_uses_provided_model_override(self, fake_client: Any) -> None:
        run_with_skill(
            task="hi",
            skill_content="be nice",
            model="custom-model",
            client=fake_client,  # type: ignore[arg-type]
        )
        call = fake_client.calls[0]
        assert call["model"] == "custom-model"
        # CompletionResult.model 应当透传
        # 通过 RunOutput.model 验证
        output = run_with_skill(
            task="hi2",
            skill_content=None,
            model="custom-model",
            client=fake_client,  # type: ignore[arg-type]
        )
        assert output.model == "custom-model"

    def test_runoutput_is_frozen(self) -> None:
        # RunOutput 是 frozen dataclass
        ro = RunOutput(
            skill_name="x", task="t", content="c", tokens=0, model="m"
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            ro.content = "modified"  # type: ignore[misc]


# -------- 集成:真实 skill + mock client --------

class TestIntegrationWithBuiltInSkills:
    """内置 skill 文件 + mock 客户端,验证 messages 拼接正确。"""

    def test_concise_skill_in_system_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        class Fake:
            def execute(
                self,
                messages: list[dict[str, str]],
                *,
                model: str | None = None,
                temperature: float = 0.7,
            ) -> CompletionResult:
                captured["messages"] = list(messages)
                return CompletionResult(
                    content="ok",
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                    model="m",
                )

            def judge(self, *args: Any, **kwargs: Any) -> Any:
                raise NotImplementedError

        monkeypatch.setattr("arena.runner.DeepSeekClient", Fake)
        run_with_skill(
            task="写一句话",
            skill_content=load_skill_by_name("concise-writer"),
            skill_name="concise-writer",
        )
        msgs = captured["messages"]
        assert msgs[0]["role"] == "system"
        assert "简洁" in msgs[0]["content"] or "Concise" in msgs[0]["content"]
        assert msgs[1]["content"] == "写一句话"