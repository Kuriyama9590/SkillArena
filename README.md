# Skill 竞技场

> 把"prompt skill（系统提示词工程）"变成可量化、可对比、可累积的资产。
> 在固定赛道任务集上，让多个 skill 引导同一执行模型生成产物，由评判模型盲评，用分赛道 Elo 累积排名。

## 这是什么

SkillArena 是一个 **Web 竞技台**：通过浏览器启动竞技、实时观看对战与流式输出、查看分赛道天梯、管理 skill、回放历史 run。

- 同一批任务，不同 skill 引导同一执行模型生成产物。
- 评判模型对两段**匿名**产物做盲评（标注 Response A / B，不泄露 skill 名）。
- 用**标准 Elo**（K=32，初始 1500）按**赛道分仓**累积排名。
- 融合 Top2 产出新 skill、自改进垫底 skill，形成"对比 → 融合 → 改进 → 再对比"的闭环。

## 权威文档

| 文档 | 作用 |
|---|---|
| **`docs/REQUIREMENTS.md`** | 当前唯一需求基线。赛道模型、选手模型、排名、竞技机制、产物与改进闭环、页面行为、现状对照。**改任何竞技相关逻辑前先读它。** |
| `tasks/todo.md` | 执行任务清单与进度 |
| `CLAUDE.md` | 给 AI 助手的架构与操作指南 |

> 注意：本 README 早期版本的 CLI/库 API 章节已过时。**主交付是 Web 应用**，CLI（`arena/__main__.py`）已降级为废弃。`deliverable-*.md` 与 `FIX_PLAN.md` 为历史交付快照，非当前真相。

## 快速开始

需要 Python 3.10+ 与 Node.js。

```bash
# 1. 安装后端依赖
pip install -e ".[dev]"

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY

# 3. 安装前端依赖
cd frontend && npm install && cd ..

# 4. 一键启动（后端 :8000 + 前端 :5173）
python scripts/dev.py
# Windows 也可用 dev.cmd
```

访问：
- 前端：http://localhost:5173 （HMR，`/api` 代理到 :8000）
- API 文档：http://localhost:8000/docs

### 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | （必填） | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | API base URL |
| `DEEPSEEK_EXECUTE_MODEL` | `deepseek-chat` | 执行任务的模型 |
| `DEEPSEEK_JUDGE_MODEL` | `deepseek-reasoner` | 评判用的模型 |
| `ARENA_TIMEOUT_SECONDS` | `120` | 单次 API 调用超时 |
| `ARENA_MAX_RETRIES` | `3` | 重试次数（指数退避） |

## 测试

```bash
python -m pytest                       # 全套（全 mock，无网络）
python -m pytest tests/test_orchestrator.py::TestRunFullCycleEndToEnd -v   # 单个测试

# 真实 API 冒烟（消耗 token，≤3 次调用）
RUN_E2E_SMOKE=1 pytest tests/test_e2e_smoke.py -v -s
```

## 架构速览

三层结构，详见 `CLAUDE.md`：

- **`arena/`** — 核心引擎包（与 HTTP 无关）。`ArenaOrchestrator.run_full_cycle()` 是主干：4 阶段 **A→B→C→D**（对比竞技 → 融合 Top2 → 自改进垫底 → 报告）。含 `judge`（盲评）/ `elo`（分赛道 Elo）/ `fuse` / `self_improve` / `skill_metadata`（skill→赛道归属）。
- **`backend/`** — FastAPI HTTP 层。`arena` router 通过 SSE `/events` 实时推事件流，`POST /run` 在线程池跑完整 cycle。进程重启后从 `reports/events/run_*.jsonl` 回放重建状态。
- **`frontend/`** — React 19 + Vite + Tailwind。`useArenaStatus.ts` 消费 SSE 重建对战/流式/排名态。

核心数据流：`orchestrator` 发事件 → 落盘 `reports/events/run_<ts>.jsonl` **同时**广播给 SSE 订阅者 → 前端实时更新。同一份 jsonl 也是回放源。

## Elo 原理

Elo 原是国际象棋打分模型，核心：一场比赛后的得分变化正比于"实际得分 − 预期得分"。

```
E_A = 1 / (1 + 10^((R_B - R_A) / 400))   # A 战胜 B 的预期概率
ΔR_A = K × (S_A - E_A)                    # K=32, 初始分 1500
S_A ∈ {1.0, 0.5, 0.0}                     # 胜/平/负
```

- 双方平分时预期各 50%；A 赢 → A +16, B −16。
- 弱胜强奖励大，强胜弱奖励小。总分守恒（ΔR_A = −ΔR_B）。
- 本项目 **Elo 只由评判的 `winner` 驱动**（维度分不进 Elo），按赛道分仓存储到 `reports/elo_state.json`，跨多次 run 累积。

## 当前状态与待重构

代码现状赛道为 `writing/coding/analysis` + `general`（3 条），`docs/REQUIREMENTS.md` 定义了迁移到 **6 条赛道**（代码/写作/推理/角色扮演/指令遵循/长文本）的目标，以及定级赛、产物超越原始版本入榜、劣化归档、软删除等尚未实现的需求。迁移工作见 `tasks/todo.md`。
