"""task_generator 模块单测:完全 mock v4-pro,不打真实网络。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from arena.deepseek_client import CompletionResult
from arena.task_generator import (
    ALLOWED_CATEGORIES,
    ALLOWED_DIFFICULTIES,
    GENERATOR_SYSTEM_PROMPT,
    Task,
    TaskGenerator,
    _extract_json_array,
    _make_auto_id,
    build_generator_messages,
)


# -------- Helpers --------


def _make_completion(content: str, model: str = "deepseek-v4-pro") -> CompletionResult:
    return CompletionResult(
        content=content,
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        model=model,
    )


def _fake_tasks_payload(
    category: str = "writing",
    difficulty: str = "medium",
    count: int = 5,
    *,
    inject_garbage_index: int | None = None,
) -> list[dict[str, Any]]:
    """构造 v4-pro 应返回的合法 JSON 数组。"""
    base = [
        {
            "id": f"{category}-auto-aaaaa{i}",
            "category": category,
            "prompt": f"测试任务 #{i + 1}:写一段关于'主题 {i + 1}'的短文,200 字以内",
            "reference": None,
            "difficulty": difficulty,
        }
        for i in range(count)
    ]
    if inject_garbage_index is not None and 0 <= inject_garbage_index < len(base):
        # 让第 N 个用非法 difficulty,触发 pydantic 校验失败
        # (id 会被 TaskGenerator 覆盖为稳定 hash,所以"缺 id"已经无法触发失败)
        base[inject_garbage_index] = {
            "id": "garbage",
            "category": category,
            "prompt": f"这是非法 difficulty 的第 {inject_garbage_index + 1} 个",
            "reference": None,
            "difficulty": "ultra_hard",  # 不在 ALLOWED_DIFFICULTIES 中
        }
    return base


class FakeClient:
    """可注入序列响应的假 client。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self._idx = 0

    def judge(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> CompletionResult:
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "temperature": temperature,
            }
        )
        if self._idx >= len(self.responses):
            content = self.responses[-1]  # 耗尽就用最后一个
        else:
            content = self.responses[self._idx]
            self._idx += 1
        return _make_completion(content, model=model or "deepseek-v4-pro")

    # runner 不会用到 execute,但为了类型完整性:
    def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


# -------- 单元:Task pydantic schema --------


class TestTaskSchema:
    def test_valid_task(self) -> None:
        t = Task(
            id="writing-auto-abc123",
            category="writing",
            prompt="写一段短文",
            difficulty="easy",
        )
        assert t.id == "writing-auto-abc123"
        assert t.category == "writing"

    def test_category_normalized(self) -> None:
        t = Task(
            id="x-1",
            category="WRITING",
            prompt="p",
            difficulty="easy",
        )
        assert t.category == "writing"

    def test_invalid_category_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Task(id="x-1", category="unknown_cat", prompt="p", difficulty="easy")

    def test_invalid_difficulty_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Task(id="x-1", category="writing", prompt="p", difficulty="super_hard")

    def test_empty_prompt_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Task(id="x-1", category="writing", prompt="   ", difficulty="easy")

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Task(id="  ", category="writing", prompt="p", difficulty="easy")


# -------- 单元:build_generator_messages / _extract_json_array --------


class TestMessagesAndExtraction:
    def test_messages_structure(self) -> None:
        msgs = build_generator_messages("writing", 3, "medium")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "writing" in msgs[0]["content"]
        assert "3" in msgs[1]["content"]
        assert "medium" in msgs[1]["content"]
        # 系统 prompt 应是常量
        assert msgs[0]["content"] == GENERATOR_SYSTEM_PROMPT.format(category="writing")

    def test_extract_pure_json(self) -> None:
        data = [{"a": 1}, {"b": 2}]
        out = _extract_json_array(json.dumps(data))
        assert out == data

    def test_extract_markdown_fence(self) -> None:
        data = [{"a": 1}]
        text = "下面是结果:\n```json\n" + json.dumps(data) + "\n```\n完毕"
        out = _extract_json_array(text)
        assert out == data

    def test_extract_embedded_brackets(self) -> None:
        data = [{"x": "y"}]
        text = "前缀乱码 " + json.dumps(data) + " 后缀乱码"
        out = _extract_json_array(text)
        assert out == data

    def test_extract_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            _extract_json_array("没有 JSON")

    def test_make_auto_id_stable(self) -> None:
        id1 = _make_auto_id("writing", "写一段短文")
        id2 = _make_auto_id("writing", "写一段短文")
        assert id1 == id2
        assert id1.startswith("writing-auto-")
        assert len(id1.split("-")[-1]) == 6


# -------- 集成:TaskGenerator.generate_batch --------


class TestGenerateBatch:
    def test_returns_n_tasks_on_clean_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _fake_tasks_payload(count=5)
        client = FakeClient([json.dumps(payload)])
        gen = TaskGenerator(client=client)  # type: ignore[arg-type]

        tasks = gen.generate_batch("writing", count=5, difficulty="medium")
        assert len(tasks) == 5
        # id 是稳定 hash,不是 v4-pro 自造
        for t in tasks:
            assert t.id.startswith("writing-auto-")
            assert t.category == "writing"
            assert t.difficulty == "medium"

    def test_ids_are_unique(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _fake_tasks_payload(count=8)
        client = FakeClient([json.dumps(payload)])
        gen = TaskGenerator(client=client)  # type: ignore[arg-type]

        tasks = gen.generate_batch("coding", count=8, difficulty="easy")
        ids = [t.id for t in tasks]
        assert len(ids) == len(set(ids)), f"id 出现重复: {ids}"

    def test_prompts_are_not_identical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _fake_tasks_payload(count=5)
        client = FakeClient([json.dumps(payload)])
        gen = TaskGenerator(client=client)  # type: ignore[arg-type]

        tasks = gen.generate_batch("writing", count=5, difficulty="medium")
        prompts = [t.prompt for t in tasks]
        # 粗略:任意两条都不完全相同
        assert len(prompts) == len(set(prompts)), f"prompt 出现完全相同: {prompts}"

    def test_invalid_items_are_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 注入 1 条缺 id 的项;总数请求 5,实际通过 4
        payload = _fake_tasks_payload(count=5, inject_garbage_index=2)
        client = FakeClient([json.dumps(payload)])
        gen = TaskGenerator(client=client)  # type: ignore[arg-type]

        tasks = gen.generate_batch("writing", count=5, difficulty="medium")
        assert len(tasks) == 4  # 1 个被丢弃
        # 所有保留的 task 都有合法 id
        for t in tasks:
            assert t.id
            assert t.id.startswith("writing-auto-")

    def test_first_parse_fails_retry_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 第一次返回乱码,第二次返回合法 JSON;应最终成功
        good_payload = _fake_tasks_payload(count=3)
        client = FakeClient(
            [
                "刚才网络抽风,以下是结果(其实没有):这不是 JSON",
                json.dumps(good_payload),
            ]
        )
        gen = TaskGenerator(client=client)  # type: ignore[arg-type]
        tasks = gen.generate_batch("writing", count=3, difficulty="medium")
        assert len(tasks) == 3
        assert client._idx == 2  # 调用了 2 次

    def test_two_parse_failures_return_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = FakeClient(["bad1", "bad2"])
        gen = TaskGenerator(client=client)  # type: ignore[arg-type]
        tasks = gen.generate_batch("writing", count=5, difficulty="medium")
        assert tasks == []
        assert client._idx == 2  # 两次都尝试

    def test_invalid_category_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = FakeClient(["[]"])
        gen = TaskGenerator(client=client)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="category"):
            gen.generate_batch("not-a-category", count=3, difficulty="medium")

    def test_invalid_difficulty_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = FakeClient(["[]"])
        gen = TaskGenerator(client=client)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="difficulty"):
            gen.generate_batch("writing", count=3, difficulty="impossible")

    def test_zero_count_raises(self) -> None:
        gen = TaskGenerator(client=FakeClient(["[]"]))  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="count"):
            gen.generate_batch("writing", count=0, difficulty="easy")

    def test_uses_default_v4_pro_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _fake_tasks_payload(count=1)
        client = FakeClient([json.dumps(payload)])
        gen = TaskGenerator(client=client)  # type: ignore[arg-type]
        gen.generate_batch("writing", count=1, difficulty="easy")
        assert client.calls[0]["model"] == "deepseek-v4-pro"
        assert client.calls[0]["temperature"] == 0.8

    def test_model_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _fake_tasks_payload(count=1)
        client = FakeClient([json.dumps(payload)])
        gen = TaskGenerator(client=client)  # type: ignore[arg-type]
        gen.generate_batch(
            "writing", count=1, difficulty="easy", model="custom-model-x"
        )
        assert client.calls[0]["model"] == "custom-model-x"

    def test_all_categories_supported(self) -> None:
        """白名单内的 category 都不会被参数校验拒绝(模型调用另外 mock)。"""
        for cat in ALLOWED_CATEGORIES:
            client = FakeClient(["[]"])
            gen = TaskGenerator(client=client)  # type: ignore[arg-type]
            # 不抛异常即通过
            gen.generate_batch(cat, count=1, difficulty="easy")

    def test_all_difficulties_supported(self) -> None:
        for diff in ALLOWED_DIFFICULTIES:
            client = FakeClient(["[]"])
            gen = TaskGenerator(client=client)  # type: ignore[arg-type]
            gen.generate_batch("writing", count=1, difficulty=diff)


# -------- 集成:TaskGenerator.save_to_fixed --------


class TestSaveToFixed:
    def test_creates_file_if_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "writing.yaml"
        gen = TaskGenerator(client=FakeClient([]))  # type: ignore[arg-type]
        tasks = [
            Task(
                id=f"writing-auto-{i:06d}",
                category="writing",
                prompt=f"任务 {i}",
                difficulty="medium",
            )
            for i in range(3)
        ]
        added = gen.save_to_fixed(tasks, target)
        assert len(added) == 3
        assert target.exists()
        # 能加载回来
        import yaml

        loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert isinstance(loaded, list)
        assert len(loaded) == 3
        assert loaded[0]["id"] == "writing-auto-000000"

    def test_merges_with_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "writing.yaml"
        import yaml

        target.write_text(
            yaml.safe_dump(
                [
                    {
                        "id": "writing-auto-existing1",
                        "category": "writing",
                        "prompt": "已有任务",
                        "reference": None,
                        "difficulty": "easy",
                    }
                ],
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

        gen = TaskGenerator(client=FakeClient([]))  # type: ignore[arg-type]
        new_tasks = [
            Task(
                id="writing-auto-existing1",  # 与已存在重复
                category="writing",
                prompt="重复任务",
                difficulty="medium",
            ),
            Task(
                id="writing-auto-new0001",
                category="writing",
                prompt="新任务",
                difficulty="hard",
            ),
        ]
        added = gen.save_to_fixed(new_tasks, target)
        # 跳过重复,只新增 1 条
        assert len(added) == 1
        assert added[0].id == "writing-auto-new0001"
        # 实际文件里有 2 条
        loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert len(loaded) == 2
        ids = {t["id"] for t in loaded}
        assert ids == {"writing-auto-existing1", "writing-auto-new0001"}
