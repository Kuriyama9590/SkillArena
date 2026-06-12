# tasks/auto/

本目录用于存放 **v4-pro 动态生成的测试任务**。

## 用途

- 与 `tasks/fixed/` 平行,后者是人工撰写、长期维护的固定任务集;
- 本目录是 v4-pro 模型自动生成、可滚动追加的"任务池",
  用于扩展示范多样性、覆盖边角场景。

## 文件命名约定

```
example-batch-001.yaml
example-batch-002.yaml
...
```

- `batch-NNN` 三位序号,按生成顺序递增;
- 文件内是 YAML 列表,每条任务的标准字段:
  - `id`: 唯一标识,格式 `{category}-auto-{6位hash}`(由 TaskGenerator 自动生成)
  - `category`: 任务类别,白名单见 `arena.task_generator.ALLOWED_CATEGORIES`
  - `prompt`: 任务原文
  - `reference`: 可选参考答案
  - `difficulty`: easy | medium | hard

## 生成方式

```python
from arena.config import get_settings
from arena.deepseek_client import DeepSeekClient
from arena.task_generator import TaskGenerator

gen = TaskGenerator(client=DeepSeekClient(get_settings()))

# 生成 10 个 writing / medium 任务
tasks = gen.generate_batch(category="writing", count=10, difficulty="medium")

# 保存到 tasks/auto/example-batch-001.yaml
gen.save_to_fixed(tasks, Path("tasks/auto/example-batch-001.yaml"))
```

## 校验 / 去重

- 所有 `Task` 实例都经过 pydantic schema 校验(白名单、非空、合法 difficulty)。
- 自动生成的 id 在保存时会基于 `category + prompt` 重新哈希,
  保证稳定且不与现有 id 碰撞。
- 在合并到固定任务集前,建议先用 `TaskDeduplicator` 过滤:

```python
from arena.task_dedup import TaskDeduplicator
dedup = TaskDeduplicator()
existing = load_existing_tasks()  # 你自己的加载函数
unique = [t for t in tasks if not dedup.is_duplicate(t, existing)]
```

## 不进入版本控制?

如果希望"生成任务不进 git",可在 `.gitignore` 加:

```
tasks/auto/*.yaml
!tasks/auto/example-batch-001.yaml
!tasks/auto/README.md
```

`example-batch-001.yaml` 留作格式示范,不会随生产更新。
