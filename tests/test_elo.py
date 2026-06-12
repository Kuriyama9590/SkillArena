"""Elo 算法单测。

覆盖:初始分、平局、A 胜、B 胜、跨多轮累计、K=32 边界、JSON 持久化。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from arena.elo import (
    DEFAULT_K,
    DEFAULT_RATING,
    DRAW,
    LOSS,
    WIN,
    RatingChange,
    expected_score,
    load_state,
    run_round,
    save_state,
    update_rating,
    update_rating_detailed,
)


# -------- expected_score --------

class TestExpectedScore:
    def test_equal_ratings_give_half(self) -> None:
        e_a, e_b = expected_score(1500, 1500)
        assert e_a == pytest.approx(0.5, abs=1e-9)
        assert e_b == pytest.approx(0.5, abs=1e-9)
        assert e_a + e_b == pytest.approx(1.0, abs=1e-9)

    def test_higher_rating_has_higher_expectation(self) -> None:
        e_a, _ = expected_score(1700, 1500)
        # 经典值: 200 分差 ≈ 0.76
        assert 0.75 < e_a < 0.77

    def test_lower_rating_has_lower_expectation(self) -> None:
        e_a, _ = expected_score(1300, 1500)
        # 200 分差反向 ≈ 0.24
        assert 0.23 < e_a < 0.25

    def test_extreme_difference_approaches_bounds(self) -> None:
        # 极高 vs 极低
        e_a, e_b = expected_score(3000, 1000)
        assert e_a > 0.9999
        assert e_b < 0.0001


# -------- update_rating --------

class TestUpdateRating:
    def test_initial_state_preserved_when_draw(self) -> None:
        # 平局时双方都不变(对称初始分)
        new_a, new_b = update_rating(1500, 1500, DRAW)
        assert new_a == pytest.approx(1500, abs=1e-9)
        assert new_b == pytest.approx(1500, abs=1e-9)

    def test_a_wins_against_equal_b(self) -> None:
        # A 胜,B 降分;K=32,初始期望都是 0.5,所以变化量 = K * (1 - 0.5) = 16
        new_a, new_b = update_rating(1500, 1500, WIN)
        assert new_a == pytest.approx(1516, abs=1e-9)
        assert new_b == pytest.approx(1484, abs=1e-9)
        # 总分守恒
        assert (new_a + new_b) == pytest.approx(3000, abs=1e-9)

    def test_b_wins_when_a_loses(self) -> None:
        # score=0 表示 A 输,所以 A 降分、B 升分
        new_a, new_b = update_rating(1500, 1500, LOSS)
        assert new_a == pytest.approx(1484, abs=1e-9)
        assert new_b == pytest.approx(1516, abs=1e-9)

    def test_underdog_win_earns_more(self) -> None:
        # 弱者(1300) 战胜 强者(1700),应该获得超过 16 分
        new_underdog, new_strong = update_rating(1300, 1700, WIN)
        assert new_underdog > 1300 + 16
        # 强者输得更多
        assert new_strong < 1700 - 16

    def test_k32_boundary_exact_values(self) -> None:
        # 显式 K=32 的边界用例
        # 双方 1500,A 赢
        a, b = update_rating(1500.0, 1500.0, 1.0, k=32.0)
        assert a == pytest.approx(1516.0, abs=1e-9)
        assert b == pytest.approx(1484.0, abs=1e-9)

    def test_invalid_score_raises(self) -> None:
        with pytest.raises(ValueError):
            update_rating(1500, 1500, 0.3)
        with pytest.raises(ValueError):
            update_rating(1500, 1500, 2.0)

    def test_total_score_conservation(self) -> None:
        # 任意 score,总分应当守恒(因为 (score + (1-score)) = 1)
        for score in (WIN, DRAW, LOSS):
            a, b = update_rating(1500, 1500, score)
            assert (a + b) == pytest.approx(3000, abs=1e-9)


# -------- update_rating_detailed --------

class TestUpdateRatingDetailed:
    def test_returns_correct_deltas(self) -> None:
        rc: RatingChange = update_rating_detailed(1500, 1500, WIN)
        assert rc.old_a == 1500
        assert rc.old_b == 1500
        assert rc.new_a == pytest.approx(1516, abs=1e-9)
        assert rc.new_b == pytest.approx(1484, abs=1e-9)
        assert rc.delta_a == pytest.approx(16, abs=1e-9)
        assert rc.delta_b == pytest.approx(-16, abs=1e-9)
        assert rc.expected_a == pytest.approx(0.5, abs=1e-9)


# -------- run_round --------

class TestRunRound:
    def test_empty_pairs_returns_empty(self) -> None:
        assert run_round([]) == {}

    def test_single_match(self) -> None:
        result = run_round([("alice", "bob", WIN)])
        # 双方都应出现
        assert set(result.keys()) == {"alice", "bob"}
        assert result["alice"] == pytest.approx(1516, abs=1e-9)
        assert result["bob"] == pytest.approx(1484, abs=1e-9)

    def test_accumulation_across_rounds(self) -> None:
        # Alice 连赢 Bob 三次。
        # 因为 Bob 分数会越来越低,Alice 的预期胜率会升高,
        # 所以后续赢的 delta 会逐渐变小(每场都比上一场少一点)。
        # 精确数学:
        #   round1: +16.000 (E_A = 0.5)
        #   round2: +14.517 (E_A ≈ 0.5461)
        #   round3: +13.230 (E_A ≈ 0.5869)
        #   合计 Alice ≈ +43.747,Bob ≈ -43.747
        pairs = [("alice", "bob", WIN)] * 3
        result = run_round(pairs)
        # Alice 应当赢得分数,精确值约 1543.747
        assert result["alice"] == pytest.approx(1500 + 43.7471336, abs=1e-3)
        # Bob 损失对称
        assert result["bob"] == pytest.approx(1500 - 43.7471336, abs=1e-3)
        # 单调性:每次赢的 delta 递减
        d1 = 16.0
        # round2 delta = 32 * (1 - E_A(round2 之前))
        # round3 delta 更小
        assert result["alice"] - 1500 < 3 * 16  # 总和小于3场各赢16分
        # 总分守恒
        assert (result["alice"] + result["bob"]) == pytest.approx(3000, abs=1e-6)
        # 同时也验证我们没有超出 3 场每场都 +16 的上限(因为 delta 递减)
        assert 43 < (result["alice"] - 1500) < 48

    def test_initial_rating_for_new_player(self) -> None:
        # 新选手默认 1500
        result = run_round([("newbie", "veteran", LOSS)])
        assert result["newbie"] == pytest.approx(DEFAULT_RATING - 16, abs=1e-9)
        assert result["veteran"] == pytest.approx(DEFAULT_RATING + 16, abs=1e-9)

    def test_custom_initial_rating(self) -> None:
        result = run_round([("x", "y", WIN)], initial_rating=1000)
        # 1000 + 16
        assert result["x"] == pytest.approx(1016, abs=1e-9)
        assert result["y"] == pytest.approx(984, abs=1e-9)

    def test_total_score_conservation_across_pairs(self) -> None:
        # 多对比赛,所有选手分数之和应当守恒(2 选手 → 总分不变;N 选手 → 总分不变)
        pairs = [
            ("a", "b", WIN),
            ("b", "c", LOSS),  # b 视角 = 0.0,b 输
            ("a", "c", DRAW),
        ]
        result = run_round(pairs)
        # 总分守恒:3 选手 * 1500 = 4500
        total = sum(result.values())
        assert total == pytest.approx(4500, abs=1e-6)

    def test_invalid_score_in_batch_raises(self) -> None:
        with pytest.raises(ValueError):
            run_round([("a", "b", 0.7)])

    def test_deterministic_order(self) -> None:
        # 同输入必须产生同输出
        pairs = [("a", "b", WIN), ("b", "c", DRAW)]
        r1 = run_round(pairs)
        r2 = run_round(pairs)
        assert r1 == r2


# -------- 持久化 --------

class TestStatePersistence:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "elo.json"
        original = {"alice": 1532.0, "bob": 1468.0, "carol": 1500.0}
        save_state(original, path)
        loaded = load_state(path)
        assert loaded == original

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "does_not_exist.json"
        assert load_state(path) == {}

    def test_load_corrupted_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.json"
        path.write_text("{this is not valid json", encoding="utf-8")
        assert load_state(path) == {}

    def test_save_creates_parent_dir(self, tmp_path: Path) -> None:
        # 嵌套不存在的目录
        path = tmp_path / "nested" / "deeper" / "elo.json"
        save_state({"a": 1500.0}, path)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"a": 1500.0}

    def test_save_filters_non_numeric(self, tmp_path: Path) -> None:
        # 传入奇怪的类型不应当崩溃,而是尽量转 float
        path = tmp_path / "elo.json"
        save_state({"a": 1500, "b": 1484.5}, path)  # type: ignore[dict-item]
        loaded = load_state(path)
        assert loaded["a"] == 1500.0
        assert loaded["b"] == 1484.5