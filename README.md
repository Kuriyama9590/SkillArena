# Skill 竞技场

> 在固定任务集上对多种 skill 写作风格进行 **Elo 对比** 的基础设施。

## 项目目的

把"prompt skill"(系统提示词工程)变成一种**可量化、可对比、可累积**的资产:

- 同一批任务,用不同 skill 引导同一执行模型生成产物。
- 用一个高阶评判模型对两段匿名产物做盲评。
- 用 **标准 Elo(K=32, 初始分 1500)** 累积排名。
- 输出 Markdown 对比报告,定位哪种 skill 在哪类任务上更强。

整个系统的核心假设:**skill 是可对比、可迭代的资产**;对比必须基于统一任务集、统一评判维度、统一匿名协议,否则排名噪声极大。

## 目录结构

```
skill竞技场/
├── arena/                        # 核心引擎包
│   ├── __init__.py
│   ├── __main__.py               # CLI 入口(run/fuse/improve/report/reset)
│   ├── config.py                 # 统一配置(API key、模型、超时、重试)
│   ├── deepseek_client.py        # DeepSeek API 客户端(OpenAI 兼容)
│   ├── runner.py                 # skill 加载 + 执行
│   ├── judge.py                  # 评判引擎(Elo 对战的"裁判")
│   ├── elo.py                    # Elo 算法 + 状态持久化
│   ├── report.py                 # Markdown 报告生成
│   ├── fuse.py                   # 融合两个 skill → v3
│   ├── self_improve.py           # 自改进循环
│   ├── orchestrator.py           # ★ 主编排器(阶段 A→B→C→D)
│   ├── task_generator.py         # 调 v4-pro 动态生成测试任务
│   └── task_dedup.py             # 任务去重(jaccard / sentence-transformers)
├── skills/                       # 内置 skill 文件(.md)
│   ├── concise-writer.md         # 简洁写作风格
│   ├── detailed-writer.md        # 详细写作风格
│   └── structured-writer.md      # 结构化写作风格
├── tasks/fixed/                  # 固定任务集
│   ├── writing.yaml              # 写作类(5 个)
│   ├── coding.yaml               # 编程类(5 个)
│   └── analysis.yaml             # 分析类(5 个)
├── tasks/auto/                   # v4-pro 动态生成的任务(自动落盘)
├── reports/                      # 输出目录
│   ├── elo_state.json            # Elo 选手状态
│   ├── orchestrator_state.json   # 主编排器状态(断点续跑)
│   ├── cache/runs/               # 阶段 A 产物缓存
│   ├── fused/                    # 阶段 B 融合产物
│   ├── improved/                 # 阶段 C 自改进产物
│   └── report_YYYYMMDD_HHMMSS.md # 阶段 D Markdown 报告
├── docs/                         # 示例文档
│   ├── fusion-examples.md
│   └── self-improve-examples.md
├── tests/                        # 单测(154 个用例)
│   ├── test_elo.py
│   ├── test_runner.py
│   ├── test_judge.py
│   ├── test_fuse.py
│   ├── test_self_improve.py
│   ├── test_task_generator.py
│   ├── test_task_dedup.py
│   ├── test_orchestrator.py      # ★ 端到端 orchestrator 单测
│   └── test_e2e_smoke.py         # ★ 真实 API 冒烟测试(RUN_E2E_SMOKE=1)
├── pyproject.toml                # 项目依赖
├── .env.example                  # 环境变量模板
└── README.md
```

## 安装

需要 Python 3.10+。

```bash
# 1) 安装依赖
pip install -e ".[dev]"

# 2) 配置环境变量
cp .env.example .env
# 编辑 .env,填入你的 DEEPSEEK_API_KEY
```

可选环境变量:

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `DEEPSEEK_API_KEY` | (必填) | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | API base URL |
| `DEEPSEEK_EXECUTE_MODEL` | `deepseek-chat` | 执行任务的模型 |
| `DEEPSEEK_JUDGE_MODEL` | `deepseek-reasoner` | 评判用的模型 |
| `ARENA_TIMEOUT_SECONDS` | `120` | 单次 API 调用的超时 |
| `ARENA_MAX_RETRIES` | `3` | 重试次数(指数退避) |

## 如何添加新 skill

在 `skills/` 下新建一个 `.md` 文件即可。文件名(去掉 `.md`)就是 skill 名称。

格式建议:

```markdown
# My Skill · 一句话定位

## 核心原则
1. ...
2. ...

## 不允许的写法
- ...

## 输出格式
- ...
```

skill 文件的**完整内容**会被作为 system message 注入到执行调用中,所以写得越具体、越结构化,执行模型的遵循度越好。**不需要改任何代码**,新 skill 在下次运行时会自动被发现。

## 如何添加新任务

在 `tasks/fixed/*.yaml` 中按以下格式追加条目:

```yaml
- id: writing-006
  category: writing
  prompt: "任务描述..."
  reference: null   # 可选参考答案
  difficulty: easy  # easy | medium | hard
```

字段说明:

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 唯一标识,推荐 `{category}-{3位序号}` |
| `category` | str | 任务类别(`writing` / `coding` / `analysis` 等) |
| `prompt` | str | 任务原文,直接喂给执行模型 |
| `reference` | str \| null | 可选参考答案,用于人工校核(不参与自动评分) |
| `difficulty` | str | 难度标签,仅用于分组/筛选 |

## 如何运行一次完整对比

### 方式 A:CLI 一行(推荐)

```bash
# 跑一次完整 run_full_cycle(阶段 A→B→C→D)
python -m arena run --skills skills/concise-writer.md skills/detailed-writer.md
```

详见下方"完整闭环使用"章节。

### 方式 B:Python API(更灵活)

```python
from arena.orchestrator import ArenaOrchestrator

orch = ArenaOrchestrator()
report = orch.run_full_cycle(
    skill_paths=["skills/concise-writer.md", "skills/detailed-writer.md"],
    task_source="fixed",
)
print(report.report_path)
```

### 方式 C:单步组合(debug / 教学用)

```python
from arena.config import get_settings
from arena.deepseek_client import DeepSeekClient
from arena.runner import run_with_skill, load_skill_by_name
from arena.judge import compare
from arena.elo import update_rating
from arena.report import MatchResult, generate_report

client = DeepSeekClient(get_settings())
task = '用一句话解释什么是设计模式'

a = run_with_skill(task, load_skill_by_name('concise-writer'),
                   skill_name='concise-writer', client=client)
b = run_with_skill(task, load_skill_by_name('detailed-writer'),
                   skill_name='detailed-writer', client=client)

v = compare(task, a.content, b.content,
            skill_a='concise-writer', skill_b='detailed-writer', client=client)
print('winner =', v.winner, 'score =', v.to_score())

new_a, new_b = update_rating(1500, 1500, v.to_score())
print('new ratings:', new_a, new_b)
```

### 方式 B:作为库使用(推荐)

```python
from arena.config import get_settings
from arena.deepseek_client import DeepSeekClient
from arena.runner import run_with_skill, load_skill_by_name
from arena.judge import compare
from arena.elo import run_round, load_state, save_state
from arena.report import MatchResult, generate_report

client = DeepSeekClient(get_settings())

records = []
pairs = [("concise-writer", "detailed-writer", 0.5)]  # 占位:实际 score 来自 compare

for task in ["写一段短文", "解释一个概念"]:
    a = run_with_skill(task, load_skill_by_name("concise-writer"),
                       skill_name="concise-writer", client=client)
    b = run_with_skill(task, load_skill_by_name("detailed-writer"),
                       skill_name="detailed-writer", client=client)
    v = compare(task, a.content, b.content, client=client)
    records.append(MatchResult(
        match_id=f"{task}#001",
        timestamp="2026-06-11T14:55:00",
        task_id=task,
        task_prompt=task,
        skill_a="concise-writer",
        skill_b="detailed-writer",
        verdict=v,
    ))

# 计算 Elo
pairs = [(r.skill_a, r.skill_b, r.verdict.to_score()) for r in records]
ratings = run_round(pairs)
save_state(ratings)

# 生成报告
generate_report(records, ratings)
```

## Elo 计算原理

Elo 原本是国际象棋用来给选手打分的模型,核心思想:**一场比赛后的得分变化,正比于"实际得分"与"预期得分"之差**。

公式:

```
E_A = 1 / (1 + 10^((R_B - R_A) / 400))   # A 战胜 B 的预期概率

ΔR_A = K × (S_A - E_A)
ΔR_B = K × (S_B - E_B) = -ΔR_A          # 总分守恒

S_A ∈ {1.0, 0.5, 0.0}                   # A 的实际得分(胜/平/负)
K = 32                                   # 标准 K 因子
R_A0 = 1500                              # 初始分
```

直观理解:

- 双方 1500 平分时,预期都是 50%。A 赢了 → A +16, B -16。
- 弱者战胜强者:弱者预期 < 50%,实际 100%,获得 **超过 16 分**(奖励大)。
- 强者战胜弱者:强者预期 > 50%,实际 100%,获得 **不足 16 分**(奖励小)。
- **K=32** 是经典 K 因子:每场比赛最多影响 ±32 分,适合样本量不大的快速迭代场景。

本项目的 Elo 状态以 JSON 持久化到 `reports/elo_state.json`,你可以跨多次运行累积分数。

## 测试

```bash
python -m pytest tests/ -v
```

当前 **154 个用例全部通过**(默认跳过 e2e),覆盖:

- Elo 算法:初始分、平局、A 胜、B 胜、跨多轮累计、K=32 边界、JSON 持久化、损坏文件恢复。
- Runner:skill 文件加载、错误路径、内置 skill 列表、messages 拼接(`system + user`)、空 skill 处理、model 透传。
- Judge:pydantic schema 校验、JSON 抽取(纯 JSON / markdown 围栏 / 文本中嵌入)、compare 端到端(成功 / 修复重试 / 两次失败)、匿名化标签、维度完整性。
- Fusion:长度硬约束(偏短重试 / 偏长截断 / 截断丢失必需要节重试)、retry-once、prompt 透传、空输入。
- Self-improve:空 weaknesses 短路、retry-once、循环提升终止条件。
- Task generator/dedup:类目白名单、JSON 抽取、jaccard 去重、可选 sentence-transformers 降级。
- Orchestrator:`run_full_cycle` 端到端(A→B→C→D)、断点续跑、Elo 更新、阶段 B/C 产物、CLI parser、输入校验、损坏 state 恢复、reset。
- E2E smoke:`RUN_E2E_SMOKE=1` 才执行,真实调 API 验证最小闭环。

所有外部 API 调用都用 mock 客户端,**不打真实网络**(e2e smoke 除外,需显式开启)。

## 已知限制

1. **当前没有"自动化多场对战"入口**:本任务只交付基础设施(单场执行 + 单场评判 + Elo 计算 + 报告生成),完整的"自动配对多场对战并产出最终报告"的 CLI/脚本由后续任务补齐。
2. **评判模型会消耗 API token**:评判使用 `deepseek-reasoner`(默认)以获得更稳定的评分,成本比 `deepseek-chat` 高。可以在 `.env` 里改 `DEEPSEEK_JUDGE_MODEL` 切到更便宜的模型,但评分稳定性会下降。
3. **judge 的 JSON 抽取是启发式的**:用三层兜底(纯 JSON / markdown 围栏 / 首对大括号),但极端情况下仍可能失败;失败时自动重试一次,若仍失败则抛 `RuntimeError`。建议在生产环境监控这类异常。
4. **Elo 没有时间衰减**:当前是简化版,跨长时间跨度不会自动 decay;后续如需"近因性更高"的排名,可以引入 K 自适应或时间衰减。
5. **execute / judge 共用一个 client 实例**:都走同一个 base_url 和超时;如果未来想用不同的服务(如 Anthropic 做 judge),需要拆分。
6. **评判维度写死在 `JUDGE_DIMENSIONS` 里**:扩展维度需要同时改 prompt 和 pydantic schema。

---

## 完整闭环使用(端到端)

`ArenaOrchestrator` 把"加载 skills → 加载/生成 tasks → 对比竞技 → 融合 → 自改进 → 报告"串成一条主流程。
CLI 入口在 `python -m arena`。

### 安装

```bash
# 1. 安装依赖
pip install -e ".[dev]"

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env,填入 DEEPSEEK_API_KEY
```

### 一次完整竞技

```bash
# PowerShell
$env:DEEPSEEK_API_KEY = "sk-xxx..."   # 已在 .env 中可省略

# 跑一次完整 run_full_cycle(阶段 A→B→C→D)
python -m arena run `
    --skills skills/concise-writer.md skills/detailed-writer.md skills/structured-writer.md `
    --task-source hybrid `
    --auto-categories writing coding analysis `
    --auto-per-category 2 `
    --rounds-per-pair 2 `
    --fused-output v3_hybrid.md `
    --max-improve-iter 2
```

或 bash / zsh:

```bash
python -m arena run \
    --skills skills/concise-writer.md skills/detailed-writer.md skills/structured-writer.md \
    --task-source hybrid \
    --auto-categories writing coding analysis \
    --auto-per-category 2 \
    --rounds-per-pair 2 \
    --fused-output v3_hybrid.md \
    --max-improve-iter 2
```

### 单独运行某一阶段

```bash
# 单独融合两个 skill(阶段 B)
python -m arena fuse \
    --a skills/concise-writer.md \
    --b skills/detailed-writer.md \
    --output skills/v3_hybrid.md

# 单独跑自改进循环(阶段 C)
python -m arena improve \
    --skill skills/concise-writer.md \
    --max-iter 3

# 重新生成报告(从已有 Elo 状态;不重跑对战)
python -m arena report

# 清空 orchestrator state(下次 run 会重跑)
python -m arena reset
```

### 作为库调用(更灵活)

```python
from arena.orchestrator import ArenaOrchestrator

orch = ArenaOrchestrator()
report = orch.run_full_cycle(
    skill_paths=[
        "skills/concise-writer.md",
        "skills/detailed-writer.md",
        "skills/structured-writer.md",
    ],
    task_source="hybrid",        # fixed / auto / hybrid
    auto_categories=["writing", "coding", "analysis"],
    auto_per_category=2,
    rounds_per_pair=2,           # 每对 (task, a, b) 跑 2 轮
)

print(f"报告:{report.report_path}")
print(f"融合:{report.fused_skill}")
print(f"自改进:{report.improvement.total_iterations} 步")
print(f"Elo:{report.elo_state}")
```

### 断点续跑

`run_full_cycle` 会在 `reports/orchestrator_state.json` 持续写状态:

- **阶段 A**:每场比赛都刷盘(防大批次中断)
- **阶段 B / C / D**:阶段切换时刷盘
- **下次启动**:从 `state.json` 读阶段状态,跳过 `status == "done"` 的阶段
- **可强制重跑**:`python -m arena reset` 删除 state.json

恢复时:再次跑 `python -m arena run ...` 即可接着上次继续。

### 端到端冒烟测试(真实 API)

```bash
# 真实调用 DeepSeek API(1 skill × 1 task × 1 round;≤3 次 API 调用)
RUN_E2E_SMOKE=1 pytest tests/test_e2e_smoke.py -v -s

# 默认(跳过占位测试,不消耗 API 配额)
pytest tests/test_e2e_smoke.py -v
```

失败时,测试会给出明确的环境检查指引(API key 是否设置、网络是否可达、目录是否可写)。

---

## 添加自己的 skill

把 `.md` 文件丢到 `skills/` 目录下,文件名(去掉 `.md`)就是 skill 名称。
**不需要改任何代码**,下次 `python -m arena run --skills ...` 时会被自动发现。

### 推荐格式

```markdown
# My Skill · 一句话定位

## 核心原则
1. ...
2. ...

## 不允许的写法
- ...

## 输出格式
- ...
```

skill 文件的**完整内容**会被作为 system message 注入到执行调用中。
写得越具体、越结构化,执行模型的遵循度越好。

### 命名约定

- 用小写 + 连字符(如 `academic-paper-writer`)
- 避免与内置 skill 重名(`concise-writer` / `detailed-writer` / `structured-writer`)

### 实战技巧

1. **在 H1 标题里点明 skill 的"一句话定位"**(评判模型可以从这里推断意图)
2. **核心原则 3-5 条,每条 1 句话**(太长会分散注意力)
3. **"不允许的写法"明确列出反例**(避免模型"打擦边球")
4. **3 个内置 skill 是参照基准**:`concise-writer`(短)、`detailed-writer`(长)、`structured-writer`(分点)
5. **先用少量任务(2-3 个)做调试**,再扩展到完整任务集

---

## 扩展任务集

任务集有 3 种来源,按"从硬编码到动态"递增灵活性:

### 方式 1:固定任务(`task_source=fixed`)

把 `.yaml` 文件加到 `tasks/fixed/`:

```yaml
- id: writing-006
  category: writing
  prompt: "用一段话总结 LLM 的局限"
  reference: null
  difficulty: medium
```

字段说明见上方表格。适合"评测集已稳定"的生产场景。

### 方式 2:动态生成(`task_source=auto`)

调 v4-pro 模型按类目生成:

```python
from arena.task_generator import TaskGenerator
from arena.task_dedup import TaskDeduplicator

gen = TaskGenerator()
dedup = TaskDeduplicator()

tasks = gen.generate_batch(category="writing", count=5, difficulty="medium")
# 自动去重 + 合并到固定任务集
gen.save_to_fixed(tasks, Path("tasks/fixed/writing.yaml"))
```

可指定 `model="deepseek-v4-pro"` 覆盖默认。适合"想快速扩大评测集"。

### 方式 3:混合(`task_source=hybrid`)

CLI:

```bash
python -m arena run --skills ... --task-source hybrid --auto-categories writing coding
```

行为:加载 `tasks/fixed/` + 动态生成 → 用 jaccard 相似度去重(>=0.85 视为重复)→ 合并用。
适合"固定集 + 增量生成"的演进场景。

### 自定义去重阈值

```python
from arena.task_dedup import TaskDeduplicator

dedup = TaskDeduplicator()
if dedup.is_duplicate(new_task, existing, threshold=0.90):
    print("太相似,跳过")
```

阈值越低越严格(0.85 是经验值,可调)。

### 推荐实践

1. **5-10 个固定任务作为"基线"**,跑多轮看趋势
2. **每 1-2 周跑一次 auto 生成**,合并进固定集
3. **重大改动前**用同一批任务对比,看 Elo 变化
4. **任务 prompt 尽量具体**,避免评判出现"两种 skill 都对/都错"的无信号结果