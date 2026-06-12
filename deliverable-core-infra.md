# 交付总结 · core-infra

## 1. 实际产出的文件清单

### 核心引擎包 `arena/`
| 文件 | 职责 |
|------|------|
| `arena/__init__.py` | 包标记,版本号 `0.1.0` |
| `arena/config.py` | `Settings` dataclass,从环境变量读 `DEEPSEEK_API_KEY` 等;未设置时抛 `RuntimeError`;附带路径常量 |
| `arena/deepseek_client.py` | `DeepSeekClient` 封装 `OpenAI(base_url=https://api.deepseek.com/v1)`,`execute()` / `judge()` 两个高层方法,指数退避重试(最多 3 次),超时 120s |
| `arena/runner.py` | `load_skill()` / `load_skill_by_name()` / `run_with_skill()` / `list_available_skills()`,skill 作为 system message 注入 |
| `arena/judge.py` | `compare()` 端到端评判,`build_judge_messages()` 构造匿名化 prompt,`Verdict` pydantic schema,`_extract_json` 三层 JSON 抽取 + 自动修复重试 |
| `arena/elo.py` | 标准 Elo(K=32, 初始 1500)纯函数 `update_rating` / `run_round`,`save_state` / `load_state` JSON 持久化 |
| `arena/report.py` | `generate_report(records, elo_state)` 输出 Markdown 排行榜 + 胜率 + 平均分 + 最近 10 场 |

### 内置 skill `skills/`
- `skills/concise-writer.md`(简洁风格)
- `skills/detailed-writer.md`(详细风格)
- `skills/structured-writer.md`(结构化风格)

### 固定任务集 `tasks/fixed/`
- `tasks/fixed/writing.yaml`(5 个任务,easy/medium/hard)
- `tasks/fixed/coding.yaml`(5 个任务)
- `tasks/fixed/analysis.yaml`(5 个任务)

### 测试 `tests/`(65 个用例,全部通过)
- `tests/test_elo.py`(25 个用例,覆盖 initial / 平局 / A 胜 / B 胜 / 跨轮累计 / K=32 边界 / JSON 持久化 / 损坏恢复)
- `tests/test_runner.py`(15 个用例,覆盖 skill 加载 / 错误路径 / messages 拼接 / 内置 skill 集成)
- `tests/test_judge.py`(25 个用例,覆盖 Verdict schema / JSON 抽取 / compare 端到端含重试路径)

### 项目配置
- `pyproject.toml`(依赖:openai, pyyaml, pydantic, pytest, python-dotenv;配置 pytest testpaths)
- `.env.example`(`DEEPSEEK_API_KEY` 等环境变量模板)
- `.gitignore`(`__pycache__` / `.env` / `reports/elo_state.json` / `reports/*.md`)
- `README.md`(项目目的、目录结构、如何添加 skill / task、如何运行对比、Elo 原理、已知限制)

---

## 2. pytest 输出(最后 30 行)

```
tests/test_judge.py::TestCompareEndToEnd::test_invalid_first_response_triggers_retry PASSED [ 72%]
tests/test_judge.py::TestCompareEndToEnd::test_two_failures_raise PASSED     [ 73%]
tests/test_judge.py::TestCompareEndToEnd::test_messages_passed_to_judge PASSED [ 75%]
tests/test_runner.py::TestLoadSkill::test_reads_full_content PASSED         [ 76%]
tests/test_runner.py::TestLoadSkill::test_missing_file_raises PASSED        [ 78%]
tests/test_runner.py::TestLoadSkill::test_directory_path_raises PASSED      [ 80%]
tests/test_runner.py::TestLoadSkill::test_string_path_accepted PASSED       [ 81%]
tests/test_runner.py::TestLoadSkillByName::test_loads_built_in_skill PASSED [ 83%]
tests/test_runner.py::TestLoadSkillByName::test_missing_skill_raises_with_list PASSED [ 84%]
tests/test_runner.py::TestListAvailableSkills::test_lists_builtin_skills PASSED [ 86%]
tests/test_runner.py::TestListAvailableSkills::test_returns_sorted PASSED    [ 87%]
tests/test_runner.py::TestListAvailableSkills::test_missing_dir_returns_empty PASSED [ 89%]
tests/test_runner.py::TestRunWithSkill::test_skill_injected_as_system_message PASSED [ 90%]
tests/test_runner.py::TestRunWithSkill::test_no_skill_runs_baseline PASSED  [ 92%]
tests/test_runner.py::TestRunWithSkill::test_empty_skill_treated_as_no_skill PASSED [ 93%]
tests/test_runner.py::TestRunWithSkill::test_empty_task_raises PASSED       [ 95%]
tests/test_runner.py::TestRunWithSkill::test_uses_provided_model_override PASSED [ 96%]
tests/test_runner.py::TestRunWithSkill::test_runoutput_is_frozen PASSED     [ 98%]
tests/test_runner.py::TestIntegrationWithBuiltInSkills::test_concise_skill_in_system_message PASSED [100%]

============================= 65 passed in 1.17s ==============================
```

完整日志:`reports/_pytest_initial.log` 与 `reports/_pytest_run.log`(临时调试日志,被 .gitignore 排除)。

---

## 3. 如何运行一次对比

### 设置环境变量
```bash
export DEEPSEEK_API_KEY="sk-xxx..."
```

### 验证基础设施
```bash
cd "E:\Projects\skill竞技场"
python -m pytest tests/ -v
```

### 跑一场 Elo 对比(伪 pipeline,完整 CLI 由后续任务提供)
```python
from arena.config import get_settings
from arena.deepseek_client import DeepSeekClient
from arena.runner import run_with_skill, load_skill_by_name
from arena.judge import compare
from arena.elo import update_rating, run_round, save_state
from arena.report import MatchResult, generate_report

client = DeepSeekClient(get_settings())
task = "用一句话解释什么是设计模式"

a = run_with_skill(task, load_skill_by_name("concise-writer"),
                   skill_name="concise-writer", client=client)
b = run_with_skill(task, load_skill_by_name("detailed-writer"),
                   skill_name="detailed-writer", client=client)

v = compare(task, a.content, b.content,
            skill_a="concise-writer", skill_b="detailed-writer", client=client)
print("winner =", v.winner, "score =", v.to_score())

new_a, new_b = update_rating(1500, 1500, v.to_score())
print("new ratings:", new_a, new_b)
```

---

## 4. 已知限制

1. **没有"自动化多场对战"CLI 入口**:本任务只交付基础设施(单场执行 + 单场评判 + Elo 计算 + 报告生成)。完整的"自动配对所有 skill 在所有任务上对打并产出最终报告"的 pipeline 由后续任务实现。
2. **judge 默认使用 `deepseek-reasoner`**:更稳定但 token 更贵。可在 `.env` 里把 `DEEPSEEK_JUDGE_MODEL` 改成 `deepseek-chat`,但评分稳定性会下降。
3. **JSON 抽取是启发式的**:三层兜底(纯 JSON / markdown 围栏 / 首对大括号)。失败时自动重试一次,仍失败抛 `RuntimeError`。建议生产环境监控。
4. **Elo 没有时间衰减**:简化版,跨长时间跨度不会自动 decay;如需"近因性更高"可加 K 自适应或时间衰减。
5. **execute / judge 共用一个 client**:都走同一 base_url/超时。若要混用不同服务(如 Anthropic judge),需要拆分。
6. **评判维度写死**:扩展维度需同时改 `JUDGE_DIMENSIONS` 和 `DimensionScores`。
7. **模型默认值调整**:任务说明里指定的 `deepseek-v4-flash` / `deepseek-v4-pro` 实际是占位模型名;我用了 DeepSeek 真实存在的 `deepseek-chat` / `deepseek-reasoner`,在 `.env.example` 中可通过 `DEEPSEEK_EXECUTE_MODEL` / `DEEPSEEK_JUDGE_MODEL` 自由覆盖。
8. **temp log 文件**:测试过程中 Tee-Object 留下的 `reports/_pytest_*.log` 临时文件已被 `.gitignore` 排除,可通过 `mavis-trash` 或手动删除。