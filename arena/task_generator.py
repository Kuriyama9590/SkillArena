"""任务生成器:调用 v4-pro 模型动态生成测试任务。

设计目标:
- 用 v4-pro 模型(默认 `deepseek-v4-pro`,可覆盖)针对给定 category/difficulty
  生成 count 个多样化、不重复的测试任务。
- 输出严格 JSON 数组,每项包含: id / category / prompt / difficulty / (可选) reference。
- 用 pydantic 严格校验,失败的项 retry 一次(最多 2 次);仍失败则跳过并记 warning。
- 提供 `save_to_fixed` 方法把生成任务合并到固定任务集 YAML。
- 类别白名单(6 条赛道): coding / writing / reasoning / roleplay / instruction / longtext。
- 所有生成任务必须带唯一 id,格式 `{category}-{auto}-{6位hash}`。

去重由 `arena.task_dedup.TaskDeduplicator` 提供,本模块不重复实现。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Sequence

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from .config import PROJECT_ROOT
from .deepseek_client import DeepSeekClient

logger = logging.getLogger(__name__)


# -------- 类目白名单(6 条赛道,与 skill_metadata.TASK_DOMAINS 对齐) --------
ALLOWED_CATEGORIES: tuple[str, ...] = (
    "coding",
    "writing",
    "reasoning",
    "roleplay",
    "instruction",
    "longtext",
)
ALLOWED_DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard")


# -------- 数据结构 --------


class Task(BaseModel):
    """单个测试任务。

    Attributes:
        id: 唯一标识,生成任务格式 `{category}-auto-{6位hash}`。
        category: 任务所属赛道,必须在 ALLOWED_CATEGORIES 内。
        prompt: 任务原文,直接喂给执行模型。
        reference: 可选参考答案。
        difficulty: 难度标签,必须在 ALLOWED_DIFFICULTIES 内。
        machine_check: 可选机检元数据。代码赛道为 `{type: "code", test_cases: [...], runner: "unittest"}`;
            指令遵循赛道为 `{type: "constraints", constraints: [{kind: "json_schema"|"regex"|"max_length", ...}]}`。
            非机检赛道(writing/reasoning/roleplay/longtext)不带此字段。详见 docs/REQUIREMENTS.md §5.3。
    """

    id: str
    category: str
    prompt: str
    reference: str | None = None
    difficulty: str
    machine_check: dict[str, Any] | None = None

    @field_validator("category")
    @classmethod
    def _validate_category(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_CATEGORIES:
            raise ValueError(
                f"category 必须是 {ALLOWED_CATEGORIES} 之一,实际为 {v!r}"
            )
        return v

    @field_validator("difficulty")
    @classmethod
    def _validate_difficulty(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_DIFFICULTIES:
            raise ValueError(
                f"difficulty 必须是 {ALLOWED_DIFFICULTIES} 之一,实际为 {v!r}"
            )
        return v

    @field_validator("prompt")
    @classmethod
    def _validate_prompt(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("prompt 不能为空")
        return v

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("id 不能为空")
        return v


# -------- Prompt 构造 --------

GENERATOR_SYSTEM_PROMPT = """你是 Skill 竞技场的"任务设计专家"。

你的职责是**生成多样化、不重复的测试任务**,用于在该赛道的 skill 之间做对比竞技。

## 任务格式(严格 JSON 数组,无任何额外文字)
```json
[
  {{
    "id": "{category}-auto-<6位字母数字>",
    "category": "{category}",
    "prompt": "<清晰、具体、有评判空间的任务描述>",
    "reference": "<可选参考答案;若不提供则设为 null>",
    "difficulty": "<easy|medium|hard>"
  }},
  ...
]
```

## 关键要求
1. **多样性**:任务之间主题、切入点、风格显著不同,不要同义改写。
2. **可评判**:任务必须能体现该赛道的能力分化(不同 skill 产出应有可辨识的高下之分),而非所有 skill 都能做好的无信号任务。
3. **id 唯一**:6 位 hash 建议基于 prompt 的短摘要生成(小写字母+数字),避免碰撞。
4. **prompt 长度**:中等复杂度(15-150 字),既要清晰也要有发挥空间。
5. **difficulty 与任务复杂度匹配**:
   - easy:单一知识点 / 直接应用。
   - medium:需要 2-3 步推理或对比。
   - hard:需要综合分析、设计或权衡。
6. **reference**:可空(写 null),不强制;若填,要简洁、有用。
7. **严格 JSON**:只输出 JSON 数组,**不要** markdown 围栏、不要解释、不要编号。

## 禁止行为
- 不要生成同一主题的近义改写。
- 不要在 prompt 中泄露评判维度(避免引导)。
- 不要在 prompt 中出现"请扮演..."等元描述(roleplay 赛道的身份设定写在 prompt 本身的任务语境里,而非元指令)。
"""

GENERATOR_USER_PROMPT_TEMPLATE = """请生成 {count} 个 category={category},difficulty={difficulty} 的测试任务。

要求:
- 主题多样化,彼此不重复
- 严格遵守 difficulty 标注
- id 格式: `{category}-auto-<6位hash>`,hash 由你基于 prompt 自行设计(用小写字母+数字)
- 输出严格的 JSON 数组,无任何额外文字

请开始。"""


def build_generator_messages(
    category: str,
    count: int,
    difficulty: str,
) -> list[dict[str, str]]:
    """构造任务生成的 messages。"""
    system = GENERATOR_SYSTEM_PROMPT.format(category=category)
    user = GENERATOR_USER_PROMPT_TEMPLATE.format(
        category=category,
        difficulty=difficulty,
        count=count,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# -------- JSON 抽取(借鉴 judge 模块的稳健性策略) --------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)


def _extract_json_array(text: str) -> list[Any]:
    """从模型回复中抽取 JSON 数组。

    三层兜底:
    1. 直接 json.loads 整段。
    2. 抽取 ```json ... ``` 围栏中的内容。
    3. 找首对匹配的方括号并尝试 loads。
    """
    text = text.strip()

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    if "[" in text and "]" in text:
        start = text.index("[")
        end = text.rindex("]") + 1
        candidate = text[start:end]
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"无法从模型回复中抽取合法 JSON 数组: {exc}; raw={text[:300]!r}..."
            ) from exc

    raise ValueError(f"模型回复中没有 JSON 数组: {text[:200]!r}")


# -------- 内部 ID 哈希工具 --------


def _make_auto_id(category: str, prompt: str) -> str:
    """根据 category + prompt 生成稳定的 6 位 hash id。"""
    h = hashlib.sha1(f"{category}|{prompt}".encode("utf-8")).hexdigest()
    return f"{category}-auto-{h[:6]}"


# -------- 主类 --------


class TaskGenerator:
    """调用 v4-pro 动态生成测试任务。

    用法:
        gen = TaskGenerator(client=DeepSeekClient(get_settings()))
        tasks = gen.generate_batch("writing", count=5, difficulty="medium")
        gen.save_to_fixed(tasks, Path("tasks/fixed/writing.yaml"))
    """

    DEFAULT_MODEL: str = "deepseek-v4-pro"
    MAX_PARSE_ATTEMPTS: int = 2  # 原始 + 一次 retry
    MAX_ITEM_RETRIES: int = 2  # 单个 item 的 retry 次数(原始+1,失败则跳过)

    def __init__(self, client: DeepSeekClient | None = None) -> None:
        self._client = client

    @property
    def client(self) -> DeepSeekClient:
        if self._client is None:
            # 懒加载:不强制在构造时校验 API key,允许纯生成场景跳过
            self._client = DeepSeekClient()
        return self._client

    # -------- 主入口 --------

    def generate_batch(
        self,
        category: str,
        count: int,
        difficulty: str,
        *,
        model: str = DEFAULT_MODEL,
    ) -> list[Task]:
        """生成一批任务。

        行为:
        1. 校验 category / difficulty 白名单。
        2. 调用模型生成 count 个任务(可能因失败项而被截短,见下)。
        3. 整体 JSON 解析失败时 retry 一次。
        4. 单个 item 校验失败时,丢弃该 item 并打 warning(不再重试单 item)。
        5. 若生成数 < count,记 warning 并返回实际生成的列表。
        6. 对所有 Task 重新计算稳定 id,避免 v4-pro 自造 id 漂移。

        Returns:
            校验通过的 Task 列表(数量 ≤ count)。
        """
        category = category.strip().lower()
        difficulty = difficulty.strip().lower()
        if category not in ALLOWED_CATEGORIES:
            raise ValueError(
                f"category 必须在白名单 {ALLOWED_CATEGORIES} 内,实际为 {category!r}"
            )
        if difficulty not in ALLOWED_DIFFICULTIES:
            raise ValueError(
                f"difficulty 必须在白名单 {ALLOWED_DIFFICULTIES} 内,实际为 {difficulty!r}"
            )
        if count <= 0:
            raise ValueError("count 必须 > 0")

        messages = build_generator_messages(category, count, difficulty)

        raw_items: list[dict[str, Any]] = []
        for attempt in range(self.MAX_PARSE_ATTEMPTS):
            try:
                result = self.client.judge(
                    messages, model=model, temperature=0.8
                )
                raw_items = _extract_json_array(result.content)
                break
            except (ValueError, ValidationError) as exc:
                logger.warning(
                    "TaskGenerator 第 %d 次解析失败: %s; raw=%s",
                    attempt + 1,
                    exc,
                    getattr(result, "content", "")[:200] if "result" in locals() else "",
                )
                if attempt + 1 >= self.MAX_PARSE_ATTEMPTS:
                    # 重试耗尽,放弃
                    logger.error("TaskGenerator 两次解析均失败,返回空列表")
                    return []
                # 追加修复指令
                messages = list(messages) + [
                    {
                        "role": "user",
                        "content": (
                            "你上一轮输出不是合法的 JSON 数组。"
                            "请**只**输出严格符合 schema 的 JSON 数组,无任何额外文字、"
                            "无 markdown 围栏。"
                        ),
                    }
                ]
            except Exception as exc:  # noqa: BLE001
                logger.error("TaskGenerator 调用模型失败: %s", exc)
                return []

        # 单 item 校验 + id 修正
        tasks: list[Task] = []
        seen_ids: set[str] = set()
        for item in raw_items:
            try:
                # 强制用稳定 id 覆盖模型自造 id
                if not isinstance(item, dict):
                    raise ValueError(f"任务项不是 dict: {item!r}")
                item = dict(item)
                item.setdefault("category", category)
                item.setdefault("difficulty", difficulty)
                prompt = str(item.get("prompt", "")).strip()
                item["id"] = _make_auto_id(category, prompt)

                task = Task.model_validate(item)
                if task.id in seen_ids:
                    logger.warning("重复 id 跳过: %s", task.id)
                    continue
                seen_ids.add(task.id)
                tasks.append(task)
            except (ValidationError, ValueError) as exc:
                logger.warning("单条任务校验失败已跳过: %s", exc)
                continue

        if len(tasks) < count:
            logger.warning(
                "TaskGenerator 请求 %d 个,实际通过 %d 个(部分 v4-pro 输出未通过校验)",
                count,
                len(tasks),
            )
        return tasks

    # -------- 合并到固定任务集 --------

    def save_to_fixed(
        self,
        tasks: Sequence[Task],
        target_file: Path,
    ) -> list[Task]:
        """把生成的任务合并到固定任务集 YAML。

        行为:
        1. 若 target_file 不存在,直接写入新文件(全量)。
        2. 若存在,加载现有任务,按 id 去重,把新任务追加进去,再写回。
        3. 返回最终被合并的"新增任务"(便于上层日志)。

        Args:
            tasks: 要保存的 Task 列表。
            target_file: YAML 目标文件路径,通常是 `tasks/fixed/<category>.yaml`。

        Returns:
            实际新增到文件中的 Task 列表(去重后)。
        """
        target_file = Path(target_file)
        target_file.parent.mkdir(parents=True, exist_ok=True)

        existing: list[dict[str, Any]] = []
        if target_file.exists():
            with target_file.open("r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, list):
                    existing = [dict(x) for x in loaded if isinstance(x, dict)]

        existing_ids: set[str] = {str(t.get("id", "")) for t in existing}
        added: list[Task] = []
        for t in tasks:
            if t.id in existing_ids:
                logger.info("save_to_fixed: 跳过已存在 id=%s", t.id)
                continue
            existing.append(t.model_dump())
            existing_ids.add(t.id)
            added.append(t)

        with target_file.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                existing,
                f,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )

        logger.info(
            "save_to_fixed: %s 新增 %d / 跳过 %d,总计 %d",
            target_file,
            len(added),
            len(tasks) - len(added),
            len(existing),
        )
        return list(added)


__all__ = [
    "ALLOWED_CATEGORIES",
    "ALLOWED_DIFFICULTIES",
    "Task",
    "TaskGenerator",
    "build_generator_messages",
    "GENERATOR_SYSTEM_PROMPT",
    "GENERATOR_USER_PROMPT_TEMPLATE",
    "PROJECT_ROOT",  # re-export for callers; harmless
]
