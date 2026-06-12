"""标准 Elo 评分算法 + 状态持久化。

设计原则:
- 纯函数:update_rating / run_round 不修改外部状态,易于单测。
- K=32(标准),初始分 1500。
- 状态以 JSON 持久化到 reports/elo_state.json,加载和保存用同名函数封装。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import ELO_STATE_FILE, ensure_reports_dir

# 标准国际象棋 Elo 的默认值
DEFAULT_K: float = 32.0
DEFAULT_RATING: float = 1500.0

# score 的合法取值
WIN: float = 1.0
DRAW: float = 0.5
LOSS: float = 0.0

VALID_SCORES: tuple[float, ...] = (WIN, DRAW, LOSS)


@dataclass(frozen=True)
class RatingChange:
    """一次 Elo 更新后的变化量。

    Attributes:
        old_a / old_b: 更新前的分数。
        new_a / new_b: 更新后的分数。
        expected_a / expected_b: 各自的预期胜率(A 的预期 + B 的预期 = 1)。
        delta_a / delta_b: 各自的变化量(new - old)。
    """

    old_a: float
    old_b: float
    new_a: float
    new_b: float
    expected_a: float
    expected_b: float
    delta_a: float
    delta_b: float


def expected_score(rating_a: float, rating_b: float) -> tuple[float, float]:
    """计算 A、B 各自的预期胜率(Elo 公式)。

    E_A = 1 / (1 + 10^((R_B - R_A)/400))

    返回 (E_A, E_B),且 E_A + E_B == 1(浮点误差可忽略)。
    """
    if rating_a == rating_b:
        return 0.5, 0.5
    # 用 10^((R_B - R_A)/400) 推导 A 的期望
    exponent = (rating_b - rating_a) / 400.0
    e_a = 1.0 / (1.0 + 10.0**exponent)
    e_b = 1.0 - e_a
    return e_a, e_b


def update_rating(
    rating_a: float,
    rating_b: float,
    score: float,
    *,
    k: float = DEFAULT_K,
) -> tuple[float, float]:
    """根据一场对战结果更新两者的 Elo 分数。

    Args:
        rating_a: A 当前分数。
        rating_b: B 当前分数。
        score: A 的实际得分,必须 ∈ {1.0, 0.5, 0.0}。
            - 1.0 表示 A 胜
            - 0.5 表示平局
            - 0.0 表示 A 负
        k: Elo K 因子,默认 32。

    Returns:
        (new_rating_a, new_rating_b),对称关系:new_b 的得分 = 1 - score。
    """
    _validate_score(score)
    e_a, e_b = expected_score(rating_a, rating_b)
    new_a = rating_a + k * (score - e_a)
    new_b = rating_b + k * ((1.0 - score) - e_b)
    return new_a, new_b


def update_rating_detailed(
    rating_a: float,
    rating_b: float,
    score: float,
    *,
    k: float = DEFAULT_K,
) -> RatingChange:
    """带详细中间结果的 Elo 更新,便于报告与调试。"""
    _validate_score(score)
    e_a, e_b = expected_score(rating_a, rating_b)
    new_a = rating_a + k * (score - e_a)
    new_b = rating_b + k * ((1.0 - score) - e_b)
    return RatingChange(
        old_a=rating_a,
        old_b=rating_b,
        new_a=new_a,
        new_b=new_b,
        expected_a=e_a,
        expected_b=e_b,
        delta_a=new_a - rating_a,
        delta_b=new_b - rating_b,
    )


def run_round(
    pairs: Iterable[tuple[str, str, float]],
    *,
    initial_rating: float = DEFAULT_RATING,
    k: float = DEFAULT_K,
) -> dict[str, float]:
    """批量执行一轮 Elo 更新。

    Args:
        pairs: 每项是 (name_a, name_b, score),score 的语义同 update_rating。
        initial_rating: 新选手的初始分。
        k: K 因子。

    Returns:
        选手名 -> 最终分数的 dict。**所有参与过本轮的选手都会出现在结果中**,
        即使他们原本不存在。

    Note:
        该函数是确定性的:输入顺序相同 → 输出相同。
        状态更新在临时 dict 上累积,不依赖全局副作用,易于测试。
    """
    ratings: dict[str, float] = {}

    def _ensure(name: str) -> float:
        if name not in ratings:
            ratings[name] = initial_rating
        return ratings[name]

    for name_a, name_b, score in pairs:
        _validate_score(score)
        r_a = _ensure(name_a)
        r_b = _ensure(name_b)
        new_a, new_b = update_rating(r_a, r_b, score, k=k)
        ratings[name_a] = new_a
        ratings[name_b] = new_b

    return ratings


# -------- 状态持久化 --------

def load_state(path: Path | None = None) -> dict[str, float]:
    """从 JSON 文件加载 Elo 状态;文件不存在时返回空 dict。"""
    path = path or ELO_STATE_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # 损坏的 JSON 不应该让系统崩溃;返回空状态由调用方决定是否报警。
        # 但记录错误日志有助于排查。
        from logging import getLogger

        getLogger(__name__).warning("无法加载 Elo 状态 %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    # 防御性:只保留 float-like 的键值对
    return {
        str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))
    }


def save_state(ratings: dict[str, float], path: Path | None = None) -> Path:
    """将 Elo 状态写入 JSON 文件。

    确保目标文件的父目录存在:若 path 为 None,使用 reports/ 下的默认位置;
    若调用方传入了自定义 path(常见于测试),则按需创建其父目录。
    """
    path = path or ELO_STATE_FILE
    if path is ELO_STATE_FILE:
        # 默认路径走 ensure_reports_dir,语义清晰
        ensure_reports_dir()
    else:
        # 自定义路径:确保父目录存在
        path.parent.mkdir(parents=True, exist_ok=True)
    # 保证 value 是标准 float(非 numpy 等)
    payload = {name: float(rating) for name, rating in ratings.items()}
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _validate_score(score: float) -> None:
    """校验 score 是合法取值(允许微小浮点误差)。"""
    if not isinstance(score, (int, float)):
        raise ValueError(f"Elo score 必须是数字,实际为 {type(score).__name__}")
    # 用 set 检查而不是 strict equality,避免 1.0 vs 1 的边界问题
    if score not in VALID_SCORES:
        raise ValueError(
            f"Elo score 必须是 {VALID_SCORES} 之一,实际为 {score!r}"
        )


__all__ = [
    "DEFAULT_K",
    "DEFAULT_RATING",
    "WIN",
    "DRAW",
    "LOSS",
    "RatingChange",
    "expected_score",
    "update_rating",
    "update_rating_detailed",
    "run_round",
    "load_state",
    "save_state",
]