"""任务去重:基于 prompt 文本相似度识别重复/近重复任务。

默认实现是 **Jaccard 相似度**(对字符 n-gram 或 word n-gram 求集合 Jaccard):
- 完全不依赖额外三方库,纯 Python。
- 对 prompt 改写、词序调整具有合理识别能力。
- 速度极快(适合"生成一批 → 立刻去重"的同步流)。

可选:如果安装了 `sentence-transformers`,可切换为基于 embedding 的余弦相似度
(更精准但需要下载模型)。通过构造参数 `use_embeddings=True` 启用,
库缺失时优雅降级为 Jaccard 并打 warning。

去重入口:
- `is_duplicate(new_task, existing, threshold)` → bool
- `add_unique(new_task, existing) → list[Task]` 把 new_task 追加到 existing(若唯一)
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any

from .task_generator import Task

logger = logging.getLogger(__name__)


# -------- 文本归一化 --------

# 中英常见停用词(轻量,够 prompt 级去重)
_STOPWORDS: frozenset[str] = frozenset(
    {
        # 英文
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "and", "or", "but", "if", "then", "else", "for", "of", "to", "in",
        "on", "at", "by", "with", "as", "this", "that", "these", "those",
        "it", "its", "we", "you", "they", "i", "he", "she", "him", "her",
        "do", "does", "did", "have", "has", "had", "can", "could", "should",
        "would", "may", "might", "will", "shall",
        # 中文常用
        "的", "了", "是", "在", "和", "与", "或", "及", "为", "为了",
        "我", "你", "他", "她", "它", "我们", "你们", "他们", "一个",
        "上", "下", "中", "里", "对", "把", "让", "请",
    }
)


def _normalize(text: str) -> str:
    """归一化:Unicode NFKC + 全角转半角 + 折叠空白 + 小写。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    # 折叠空白
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _tokenize_words(text: str) -> set[str]:
    """英文按词、中文按字符切;统一小写、去停用词、长度 >=2。

    设计取舍:
    - 中文没有显式分词器,逐字符拆;字符粒度的 Jaccard 对短 prompt 已经够用。
    - 英文按空白 + 标点切词。
    """
    text = _normalize(text)
    if not text:
        return set()

    # 抽取 ASCII 词 + 其余字符(中文/数字/标点逐字符)
    out: set[str] = set()
    # 英文/数字词
    for m in re.finditer(r"[a-z0-9]+", text):
        w = m.group(0)
        if len(w) >= 2 and w not in _STOPWORDS:
            out.add(w)
    # 中文字符 / 标点
    for ch in text:
        # 中文字符:ord 0x4e00 ~ 0x9fff
        if "\u4e00" <= ch <= "\u9fff":
            if ch not in _STOPWORDS:
                out.add(ch)
        # 数字单独成 token(若被中英混排拆出)
        elif ch.isdigit():
            out.add(ch)
    return out


def _shingles(text: str, n: int = 3) -> set[str]:
    """字符 n-gram 集合(对中英文都鲁棒)。"""
    text = _normalize(text)
    if len(text) < n:
        return {text} if text else set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


# -------- 相似度函数 --------


def jaccard_similarity(a: str, b: str) -> float:
    """基于 word+shingle 的 Jaccard 相似度,返回 [0, 1]。

    算法:对两个文本分别取 (词集合 ∪ 3-gram 集合),求 Jaccard。
    这样能同时捕获"词级别"和"局部顺序"两个维度的重复度。
    """
    sa = _tokenize_words(a) | _shingles(a, 3)
    sb = _tokenize_words(b) | _shingles(b, 3)
    if not sa and not sb:
        return 1.0  # 两个空文本视为完全相同
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    union = sa | sb
    return len(inter) / len(union)


# -------- Embedding 后端(可选) --------


class _EmbeddingBackend:
    """sentence-transformers 封装。库缺失时 enabled=False。

    设计:懒加载,避免在不需要 embedding 时就强制 import 重型库。
    """

    def __init__(self) -> None:
        self._model: Any | None = None
        self._enabled: bool = False
        self._err: str | None = None
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

            self._SentenceTransformer = SentenceTransformer
            self._enabled = True
        except Exception as exc:  # noqa: BLE001
            self._err = repr(exc)
            logger.info("sentence-transformers 不可用,降级为 Jaccard: %s", exc)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not self._enabled:
            raise RuntimeError("sentence-transformers 未启用")
        if self._model is None:
            self._model = self._SentenceTransformer(
                "paraphrase-multilingual-MiniLM-L12-v2"
            )
        # 缓存友好:model.encode 内部已 batch
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# -------- 主类 --------


class TaskDeduplicator:
    """任务去重器。

    行为:
    - 默认用 Jaccard 相似度(无需重型依赖)。
    - 可选 sentence-transformers 余弦相似度(`use_embeddings=True`)。
    - 缓存每条 task 的"prompt → 特征",重复比较时直接命中。
    - `add_unique` 便于在循环里逐步扩展任务集。
    """

    def __init__(self, use_embeddings: bool = False) -> None:
        self._use_embeddings = use_embeddings
        self._embedder = _EmbeddingBackend() if use_embeddings else None
        if use_embeddings and not self._embedder.enabled:
            logger.warning(
                "use_embeddings=True 但 sentence-transformers 不可用,已降级为 Jaccard"
            )
        # 缓存:prompt -> 特征(embedding vector 或 set hash)
        self._cache: dict[str, Any] = {}

    # -------- 内部:特征提取 --------

    def _features(self, text: str) -> Any:
        """根据是否启用 embedding,返回不同的特征。"""
        if text in self._cache:
            return self._cache[text]
        if self._use_embeddings and self._embedder and self._embedder.enabled:
            vec = self._embedder.encode([text])[0]
        else:
            vec = _tokenize_words(text) | _shingles(text, 3)
        self._cache[text] = vec
        return vec

    def _sim(self, fa: Any, fb: Any) -> float:
        """根据特征类型计算相似度。"""
        if isinstance(fa, list) and isinstance(fb, list):
            return _cosine(fa, fb)
        if isinstance(fa, set) and isinstance(fb, set):
            if not fa and not fb:
                return 1.0
            if not fa or not fb:
                return 0.0
            inter = fa & fb
            union = fa | fb
            return len(inter) / len(union)
        # 类型不匹配(不应该发生):保守返回 0
        logger.warning("特征类型不匹配,返回 0: %s vs %s", type(fa), type(fb))
        return 0.0

    # -------- 主 API --------

    def is_duplicate(
        self,
        new_task: Task,
        existing_tasks: Iterable[Task],
        threshold: float = 0.85,
    ) -> bool:
        """判断 new_task 是否与 existing 中任意一条重复(超过 threshold)。"""
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold 必须在 [0, 1],实际为 {threshold!r}")

        new_prompt = new_task.prompt
        if not new_prompt.strip():
            return False

        new_feat = self._features(new_prompt)

        for t in existing_tasks:
            if t.id == new_task.id:
                # 同 id 视为重复(不依赖文本相似度)
                return True
            feat = self._features(t.prompt)
            sim = self._sim(new_feat, feat)
            if sim >= threshold:
                logger.debug(
                    "duplicate detected: id=%s vs id=%s sim=%.3f (>=%.3f)",
                    new_task.id,
                    t.id,
                    sim,
                    threshold,
                )
                return True
        return False

    def add_unique(
        self,
        new_task: Task,
        existing: list[Task],
        threshold: float = 0.85,
    ) -> list[Task]:
        """若 new_task 与 existing 不重复,追加并返回新列表;否则返回只读拷贝。

        设计:总是返回新列表(避免 in-place 修改造成的隐式副作用)。
        调用方可以直接 `existing = dedup.add_unique(new, existing)`。
        """
        if not self.is_duplicate(new_task, existing, threshold=threshold):
            return [*existing, new_task]
        logger.info(
            "add_unique: 跳过重复 id=%s prompt=%r",
            new_task.id,
            new_task.prompt[:50],
        )
        # 不追加时也返回新拷贝,避免任何"引用复用"的隐式假设
        return list(existing)

    def deduplicate(
        self,
        tasks: Sequence[Task],
        threshold: float = 0.85,
    ) -> list[Task]:
        """对一批 tasks 内部去重,返回去重后的列表(保持原顺序)。"""
        out: list[Task] = []
        for t in tasks:
            if not self.is_duplicate(t, out, threshold=threshold):
                out.append(t)
        return out

    # -------- 工具方法(便于测试) --------

    def clear_cache(self) -> None:
        """清空特征缓存(测试 / 长流程内存管理用)。"""
        self._cache.clear()

    @property
    def backend_name(self) -> str:
        if self._use_embeddings and self._embedder and self._embedder.enabled:
            return "sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2)"
        return "jaccard (word+3-gram)"

    # 暴露底层,便于测试用例直接验证
    @staticmethod
    def similarity(a: str, b: str) -> float:
        """直接计算两段文本的相似度(走默认 jaccard 路径)。"""
        return jaccard_similarity(a, b)


__all__ = [
    "TaskDeduplicator",
    "jaccard_similarity",
]
