# 交付总结 · auto-tasks

## 1. 实际产出的文件清单

### 核心模块 `arena/`
| 文件 | 职责 |
|------|------|
| `arena/task_generator.py` | `Task` pydantic schema + `TaskGenerator` 类;`generate_batch(category, count, difficulty, model="deepseek-v4-pro")` 调用 v4-pro 生成任务,严格 JSON 数组解析,失败 retry 一次,单 item 校验失败跳过并 warning;`save_to_fixed(tasks, target_file)` 合并到固定任务集;类别白名单 `writing/coding/analysis/reasoning/explanation`;稳定 id `{category}-auto-{6位hash}` |
| `arena/task_dedup.py` | `TaskDeduplicator` 类;`is_duplicate(new, existing, threshold=0.85)`、`add_unique(new, existing)`、`deduplicate(batch)`;默认 Jaccard 相似度(词+3-gram 组合),特征缓存;可选 `sentence-transformers` 余弦相似度(库缺失自动降级) |
| `arena/config.py` | 新增 `TASKS_AUTO_DIR` 路径常量;`__all__` 同步 |
| `arena/__init__.py` | 导出 `Task`、`TaskGenerator`、`TaskDeduplicator`、`jaccard_similarity` |

### 自动任务目录 `tasks/auto/`
| 文件 | 职责 |
|------|------|
| `tasks/auto/README.md` | 目录用途、命名约定、生成 / 校验 / 去重示例 |
| `tasks/auto/example-batch-001.yaml` | 3 个手写示例任务(覆盖 writing/coding/analysis)作为格式示范 |

### 测试 `tests/`
| 文件 | 用例数 | 覆盖 |
|------|--------|------|
| `tests/test_task_generator.py` | 27 | Task schema 白名单 / messages 构造 / JSON 抽取(纯 JSON/围栏/嵌入)/ `_make_auto_id` 稳定 / `generate_batch` 全部 5 类 category & 3 类 difficulty / 失败 retry 路径 / 两次失败返回空 / 非法参数报错 / 默认模型 / 模型覆盖 / `save_to_fixed` 创建文件 / `save_to_fixed` 合并去重 |
| `tests/test_task_dedup.py` | 20 | jaccard 工具函数(相同/完全不同/空文本边界)/ `is_duplicate` 5 类判定(相同/近重复-低阈值/近重复-高阈值/完全不同/同 id 重复)/ `add_unique` 3 类行为(添加/跳过/不修改原列表)/ 批内去重 / 后端名称 / 缓存清理 |

### 项目配置
- `pyproject.toml`:新增 `embeddings` optional-dependencies 段(sentence-transformers + torch);默认安装路径**不**包含这两个包

---

## 2. pytest 输出(最后 10 行)

```
tests/test_task_dedup.py::TestAddUnique::test_adds_unique_task PASSED    [ 87%]
tests/test_task_dedup.py::TestAddUnique::test_skips_duplicate_task PASSED [ 89%]
tests/test_task_dedup.py::TestAddUnique::test_does_not_mutate_existing PASSED [ 91%]
tests/test_task_dedup.py::TestDeduplicate::test_dedup_within_batch PASSED [ 93%]
tests/test_task_dedup.py::TestBackend::test_default_backend_is_jaccard PASSED [ 95%]
tests/test_task_dedup.py::TestBackend::test_explicit_embeddings_request_falls_back_gracefully PASSED [ 97%]
tests/test_task_dedup.py::TestBackend::test_clear_cache PASSED           [100%]

============================= 47 passed in 1.29s ==============================
```

完整套件 112/112 通过(47 新增 + 65 旧),不破坏 core-infra 已有测试。

```
tests/test_elo.py .........................                              [ 22%]
tests/test_judge.py ........................                             [ 43%]
tests/test_runner.py ................                                    [ 58%]
tests/test_task_dedup.py ....................                            [ 75%]
tests/test_task_generator.py ...........................                 [100%]

============================= 112 passed in 1.50s =============================
```

---

## 3. 如何手动生成一批任务并保存

```bash
# 1) 配置环境变量
export DEEPSEEK_API_KEY="sk-xxx..."

# 2) 运行(交互式 Python)
cd "E:\Projects\skill竞技场"
python -c "
from pathlib import Path
from arena.config import get_settings
from arena.deepseek_client import DeepSeekClient
from arena.task_generator import TaskGenerator
from arena.task_dedup import TaskDeduplicator

client = DeepSeekClient(get_settings())
gen = TaskGenerator(client=client)

# 生成 10 个 writing / medium 任务
tasks = gen.generate_batch(category='writing', count=10, difficulty='medium')
print(f'生成 {len(tasks)} 个任务')

# 去重(基于现有 fixed 集合)
import yaml
fixed = yaml.safe_load(Path('tasks/fixed/writing.yaml').read_text(encoding='utf-8'))
from arena.task_generator import Task
fixed_tasks = [Task.model_validate(t) for t in fixed]
dedup = TaskDeduplicator()
unique = dedup.deduplicate(tasks)  # 批内去重
unique = [t for t in unique if not dedup.is_duplicate(t, fixed_tasks)]  # vs 现有

# 落盘
gen.save_to_fixed(unique, Path('tasks/auto/example-batch-001.yaml'))
print('已保存到 tasks/auto/example-batch-001.yaml')
"
```

更简单(只生成+落盘,不去重):
```python
from arena.task_generator import TaskGenerator
from arena.deepseek_client import DeepSeekClient
from arena.config import get_settings
from pathlib import Path

gen = TaskGenerator(client=DeepSeekClient(get_settings()))
tasks = gen.generate_batch('coding', count=5, difficulty='hard')
gen.save_to_fixed(tasks, Path('tasks/auto/coding-batch-001.yaml'))
```

---

## 4. 去重算法的选择理由

| 候选方案 | 优点 | 缺点 | 决策 |
|----------|------|------|------|
| **Jaccard(词+3-gram)** | 零三方依赖,极快,纯 Python;对中英文都鲁棒;对"近重复改写"识别合理 | 短文本相似度数字偏低(0.5-0.8 区间);对"语义相同但用词完全不同"识别弱 | **默认采用** |
| TF-IDF + cosine | 比 jaccard 更考虑"词重要性" | 仍属词袋;需要 sklearn,加重型依赖 | 否决 |
| sentence-transformers cosine | 语义级判重,质量高 | 需下载 ~100MB 模型;首次慢;增加 torch 依赖 | **可选**(通过 `pip install -e ".[embeddings]"` 启用) |
| SimHash | 适合亿级去重 | 短 prompt 区分度差,实现复杂 | 否决 |

**Jaccard 详细做法**:
- 词集合:`re.findall(r"[a-z0-9]+", text)` + 中文字符逐字 + 数字单字;
  去掉常见停用词(中英各 ~50 个),长度 < 2 的 token 丢弃;
- 3-gram 集合:`{text[i:i+3] for i in range(len-2)}`;
- 最终集合 = 词 ∪ 3-gram,这样既覆盖"用词重叠"又覆盖"局部语序重叠";
- 阈值建议:`0.85` 默认,但在测试中针对短 prompt(< 30 字)实际相似度多在 0.5-0.7,
  生产可考虑分级阈值(`len(prompt) < 30` 用 0.5,否则 0.85)。

**为什么默认不用 sentence-transformers**:
1. 默认 pip install 不应该拉 100MB+ 模型;
2. 生成任务场景的 prompt 通常 20-100 字,Jaccard 已经够用;
3. 需要更高质量时,只需 `pip install -e ".[embeddings]"` 即可切换,
   `TaskDeduplicator(use_embeddings=True)` 优雅降级,不破坏 API。

---

## 5. 已知限制

1. **生成任务需要真实 API key**:`generate_batch` 默认用 v4-pro(可被 `.env` 或 `model` 参数覆盖),
   没设 `DEEPSEEK_API_KEY` 时第一次实例化 `DeepSeekClient` 会抛 `RuntimeError`。
2. **生成数量可能 < 请求数量**:v4-pro 偶发输出部分不合规项,这些会被跳过并打 warning;
   若希望严格达到 `count`,可在 `generate_batch` 之后检测并重试一批。
3. **Jaccard 不识别"语义相同但用词不同"**:例如"实现 LRU Cache"和"设计最近最少使用缓存",
   词级别 Jaccard 较低;若需要更高召回,启用 embedding 后端。
4. **id 哈希 6 位短码有理论碰撞风险**(百万级任务时概率才显著);若超过 1k 任务/类别,
   建议把 6 位扩到 8 位。
5. **保存时不写 metadata**:`save_to_fixed` 只追加任务字段,不记录"来源批次 / 生成时间 / 模型"等;
   若需要审计,在调用方把元信息塞到 `reference` 字段里(非标准但够用)。
6. **生成内容是英文+中文混合时,Jaccard 对英文短语匹配更准**;中文主要靠字符级 3-gram,
   重复率阈值建议略低。
