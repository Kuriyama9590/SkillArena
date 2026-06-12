"""Skill竞技场:在固定任务集上对 skill 进行 Elo 对比的核心引擎。"""

from .orchestrator import ArenaOrchestrator, FullReport  # noqa: F401
from .task_generator import Task, TaskGenerator  # noqa: F401
from .task_dedup import TaskDeduplicator, jaccard_similarity  # noqa: F401

__version__ = "0.2.0"
