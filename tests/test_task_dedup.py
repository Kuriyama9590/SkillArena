"""task_dedup 模块单测:Jaccard 相似度 + TaskDeduplicator 行为。"""
from __future__ import annotations

import pytest

from arena.task_dedup import TaskDeduplicator, jaccard_similarity
from arena.task_generator import Task


# -------- 工具函数:jaccard_similarity --------


class TestJaccardSimilarity:
    def test_identical_text_is_one(self) -> None:
        s = "写一段关于远程办公利弊的短文,200 字以内"
        assert jaccard_similarity(s, s) == 1.0

    def test_completely_different_is_zero(self) -> None:
        a = "写一段关于远程办公的短文"
        b = "分析某 SaaS 产品月留存下降的原因"
        sim = jaccard_similarity(a, b)
        # 不要求严格 0(可能有数字/常见词碰撞),但必须 < 0.3
        assert sim < 0.3

    def test_both_empty_is_one(self) -> None:
        assert jaccard_similarity("", "") == 1.0

    def test_one_empty_is_zero(self) -> None:
        assert jaccard_similarity("hello world", "") == 0.0
        assert jaccard_similarity("", "hello world") == 0.0

    def test_near_duplicate_high(self) -> None:
        a = "为一家精品咖啡店写一段 200 字以内的品牌故事,突出'手冲'与'产地直供'两个卖点"
        b = "为一家精品咖啡店写一段 200 字以内的品牌故事,突出手冲与产地直供两个卖点"
        sim = jaccard_similarity(a, b)
        # 短文本的 jaccard 不会很高,但应当显著高于 0.3
        assert sim >= 0.5, f"期望近重复相似度 >= 0.5,实际 {sim:.3f}"


# -------- TaskDeduplicator:is_duplicate --------


def _t(pid: str, prompt: str, category: str = "writing") -> Task:
    return Task(
        id=pid,
        category=category,
        prompt=prompt,
        difficulty="medium",
    )


class TestIsDuplicate:
    def test_identical_text_detected_as_duplicate(self) -> None:
        dedup = TaskDeduplicator()
        text = "用 Python 写一个 is_palindrome 函数"
        existing = [_t("coding-001", text)]
        new = _t("coding-002", text)
        assert dedup.is_duplicate(new, existing) is True

    def test_near_duplicate_detected_at_low_threshold(self) -> None:
        """近重复文本在合理阈值下应被识别;具体阈值随文本长度而定。"""
        dedup = TaskDeduplicator()
        existing = [
            _t(
                "writing-001",
                "为一家精品咖啡店写一段 200 字以内的品牌故事,突出'手冲'与'产地直供'两个卖点",
            )
        ]
        new = _t(
            "writing-002",
            "为一家精品咖啡店写一段 200 字以内的品牌故事,突出手冲与产地直供两个卖点",
        )
        # threshold=0.5 在短文本上是合理的近重复判定点
        assert dedup.is_duplicate(new, existing, threshold=0.5) is True

    def test_near_duplicate_not_detected_at_higher_threshold(
        self,
    ) -> None:
        """近重复文本在极高阈值(0.99)下可能不被判定为重复——这是设计取舍。"""
        dedup = TaskDeduplicator()
        existing = [
            _t(
                "writing-001",
                "为一家精品咖啡店写一段 200 字以内的品牌故事,突出'手冲'与'产地直供'两个卖点",
            )
        ]
        new = _t(
            "writing-002",
            "为一家精品咖啡店写一段 200 字以内的品牌故事,突出手冲与产地直供两个卖点",
        )
        # threshold=0.99 极严苛时,短文本的 jaccard 难以达到
        assert dedup.is_duplicate(new, existing, threshold=0.99) is False

    def test_completely_different_not_duplicate(self) -> None:
        dedup = TaskDeduplicator()
        existing = [_t("writing-001", "写一段关于远程办公的短文,200 字以内")]
        new = _t(
            "reasoning-001",
            "某 SaaS 产品月留存从 45% 跌到 30%,请分析可能原因",
        )
        assert dedup.is_duplicate(new, existing) is False

    def test_same_id_always_duplicate(self) -> None:
        dedup = TaskDeduplicator()
        existing = [_t("writing-001", "完全不相关的文本 A")]
        new = _t("writing-001", "完全不相关的文本 B")
        # 同 id 即视为重复
        assert dedup.is_duplicate(new, existing) is True

    def test_empty_text_in_jaccard_returns_zero(self) -> None:
        """jaccard 工具函数对一边为空的边界情况应返回 0.0。"""
        from arena.task_dedup import jaccard_similarity

        assert jaccard_similarity("真实任务描述", "") == 0.0
        assert jaccard_similarity("", "真实任务描述") == 0.0

    def test_invalid_threshold_raises(self) -> None:
        dedup = TaskDeduplicator()
        with pytest.raises(ValueError):
            dedup.is_duplicate(_t("a", "p"), [], threshold=1.5)
        with pytest.raises(ValueError):
            dedup.is_duplicate(_t("a", "p"), [], threshold=-0.1)

    def test_caching_yields_same_result(self) -> None:
        """多次比较应命中特征缓存,结果一致。"""
        dedup = TaskDeduplicator()
        existing = [
            _t("writing-001", "解释设计模式中的工厂模式,200 字以内"),
        ]
        # 调用 1
        first = dedup.is_duplicate(
            _t("writing-002", "解释设计模式中的工厂模式,200 字以内"),
            existing,
        )
        # 调用 2,使用**不同**的 prompt
        second = dedup.is_duplicate(
            _t("writing-003", "对比 MySQL 和 PostgreSQL 的关键差异"),
            existing,
        )
        assert first is True
        assert second is False
        # 缓存里至少应有 2 个不同的 prompt key
        assert len(dedup._cache) >= 2  # type: ignore[attr-defined]


# -------- TaskDeduplicator:add_unique --------


class TestAddUnique:
    def test_adds_unique_task(self) -> None:
        dedup = TaskDeduplicator()
        existing = [_t("writing-001", "写一段关于远程办公利弊的短文")]
        new = _t("writing-002", "用 Python 实现 LRU Cache")
        out = dedup.add_unique(new, existing)
        assert len(out) == 2
        assert out[-1] is new

    def test_skips_duplicate_task(self) -> None:
        dedup = TaskDeduplicator()
        text = "写一段关于远程办公利弊的短文"
        existing = [_t("writing-001", text)]
        new = _t("writing-002", text)
        out = dedup.add_unique(new, existing)
        # 长度不变
        assert len(out) == 1
        # 返回的是新列表(不是原引用)以避免 in-place 副作用
        assert out is not existing

    def test_does_not_mutate_existing(self) -> None:
        dedup = TaskDeduplicator()
        existing = [_t("writing-001", "A")]
        original_len = len(existing)
        original_ids = [t.id for t in existing]
        new = _t("writing-002", "B")
        _ = dedup.add_unique(new, existing)
        # 原始列表不应被改
        assert len(existing) == original_len
        assert [t.id for t in existing] == original_ids


# -------- TaskDeduplicator:deduplicate(批内去重) --------


class TestDeduplicate:
    def test_dedup_within_batch(self) -> None:
        dedup = TaskDeduplicator()
        text = "用 Python 实现 is_palindrome 函数"
        batch = [
            _t("coding-001", text),
            _t("coding-002", "分析某 SaaS 月留存下降原因"),
            _t("coding-003", text),  # 与 001 完全相同
            _t("coding-004", "设计 LRU Cache"),
        ]
        out = dedup.deduplicate(batch)
        assert len(out) == 3
        ids = [t.id for t in out]
        assert "coding-003" not in ids
        assert ids[0] == "coding-001"


# -------- 后端信息 --------


class TestBackend:
    def test_default_backend_is_jaccard(self) -> None:
        dedup = TaskDeduplicator()
        assert "jaccard" in dedup.backend_name.lower()

    def test_explicit_embeddings_request_falls_back_gracefully(
        self,
    ) -> None:
        """在没有 sentence-transformers 的环境下,显式请求 embedding 应降级为 jaccard。"""
        dedup = TaskDeduplicator(use_embeddings=True)
        # 即便显式要求,也不应抛错
        assert "jaccard" in dedup.backend_name.lower() or "embedding" in dedup.backend_name.lower()

    def test_clear_cache(self) -> None:
        dedup = TaskDeduplicator()
        dedup._cache["foo"] = "bar"  # type: ignore[attr-defined]
        assert len(dedup._cache) > 0  # type: ignore[attr-defined]
        dedup.clear_cache()
        assert len(dedup._cache) == 0  # type: ignore[attr-defined]
