# Skill 竞技场 - 全流程监控修复计划

> **备注：修复完成后删除本文件。**

---

## 总览

本次修复涉及 10 个文件，按优先级分为 5 层共 20+ 问题。请按顺序执行，每层完成后跑 `npm run build` + `python -m pytest` 验证。

---

## 层 0：修复前验证

```bash
# 后端测试
python -m pytest --tb=short -q

# 前端类型检查
cd frontend && npx tsc --noEmit
```

---

## 层 1：后端核心修复（3 个文件）

### 1a. `arena/orchestrator.py` — BUG 13：进度条计数少乘 tasks 数

**位置**：第 684-694 行，`total_expected_matches` 预计算。

**问题**：当前公式 `_pairs * rounds_per_pair` 漏乘了 `len(domain_tasks)`（`_dt`）。实际每个 task 都要跑所有 pair，所以总场数 = `len(tasks) * len(pairs) * rounds`。

**修复**：把第 686-694 行改为：

```python
        total_expected_matches = 0
        for _d in TASK_DOMAINS:
            _ds = {n: s for n, s in skills.items() if s.participates_in(_d)}
            _dt = [t for t in tasks if t.get("category", "unknown") == _d]
            if len(_ds) >= 1 and _dt:
                _pairs = len(list(combinations(list(_ds.keys()) + [f"baseline_{_d}"], 2)))
                total_expected_matches += len(_dt) * _pairs * rounds_per_pair
```

改完后验证：
```bash
python -m pytest tests/test_orchestrator.py -q
```

---

### 1b. `backend/routers/arena.py` — BUG 6,7,8,9 — 请求模型缺字段 + 线程安全 + 状态滞后

#### BUG 6：RunRequest 缺 `auto_categories` 和 `auto_per_category`

在第 52 行前加两个字段：

```python
class RunRequest(BaseModel):
    skills: list[str] | None = None
    task_source: str = "fixed"
    rounds_per_pair: int = 2
    max_improve_iterations: int = 2
    run_fusion: bool = True
    run_improvement: bool = True
    auto_categories: list[str] | None = None      # 新增
    auto_per_category: int = 3                     # 新增
```

#### BUG 7：lambda 没有转发两新字段

找到第 258-265 行（`run_full_cycle` 的 lambda 调用），补充 `auto_categories` 和 `auto_per_category`：

```python
            await loop.run_in_executor(
                None,
                lambda: orch.run_full_cycle(
                    skill_paths=skill_paths,
                    task_source=req.task_source,
                    auto_categories=req.auto_categories,
                    auto_per_category=req.auto_per_category,
                    rounds_per_pair=req.rounds_per_pair,
                    max_improve_iterations=req.max_improve_iterations,
                    run_fusion=req.run_fusion,
                    run_improvement=req.run_improvement,
                ),
            )
```

#### BUG 8：`_emit_event` 线程安全

**问题**：`_emit_event` 在 executor 线程里调用：
1. 操作 `_event_subscribers` 列表（迭代+删除）
2. 调用 `asyncio.Queue.put_nowait()`（asyncio.Queue 不是线程安全的）

**修复**：用 `threading.Lock` 保护 subscriber 列表，用 `call_soon_threadsafe` 把事件投递回 event loop。

在文件顶部加 import：
```python
import threading
```

在 `_event_subscribers` 声明下方加锁：
```python
_event_subscribers: list[asyncio.Queue] = []
_event_lock = threading.Lock()
```

重写 `_emit_event`（第 79-113 行）：
```python
def _emit_event(event: dict[str, Any]) -> None:
    if "ts" not in event:
        event = {**event, "ts": _now_iso()}

    _append_event_to_file(event)

    payload = json.dumps(event, ensure_ascii=False)
    msg = f"data: {payload}\n\n"

    # 线程安全：锁保护 subscriber 读，通过 call_soon_threadsafe 投递到 event loop
    with _event_lock:
        queued = list(_event_subscribers)

    loop = asyncio.get_event_loop() if _thread_local_loop is None else _thread_local_loop

    def _put_loop(q: asyncio.Queue) -> None:
        try:
            loop.call_soon_threadsafe(q.put_nowait, msg)
        except asyncio.QueueFull:
            pass

    for q in queued:
        try:
            _put_loop(q)
        except Exception:
            pass
```

在文件顶部加 thread_local 存储 loop：
```python
_loop: Any = None

def _set_loop() -> None:
    global _loop
    _loop = asyncio.get_event_loop()
```

在 SSE 事件生成器（第 192 行附近）开始时调用 `_set_loop()`：
```python
@router.get("/events")
async def arena_events():
    _set_loop()  # 新增
    ...
```

在 unsubscribe 位置加锁：
```python
async def event_generator():
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    with _event_lock:                          # 加锁
        _event_subscribers.append(q)
    try:
        ...
    finally:
        with _event_lock:                      # 加锁
            if q in _event_subscribers:
                _event_subscribers.remove(q)
```

#### BUG 9：`_update_status_from_event` 缺失 B/C/D 事件处理

在第 107-159 行的 `_update_status_from_event` 函数中添加以下else-if分支：

```python
    elif et == "phase_b_fuse_start":
        _active_status["phase"] = "B"
        _active_status["domain"] = event.get("domain")

    elif et == "phase_c_improve_start":
        _active_status["phase"] = "C"
        _active_status["domain"] = event.get("domain")

    elif et == "phase_done":
        # 阶段完成后清除领域信息
        _active_status["domain"] = None
        _active_status["current_battle"] = None

    elif et == "run_start":
        _active_status["running"] = True
        _active_status["phase"] = None

    elif et == "run_end":
        _active_status["running"] = False
        _active_status["phase"] = None
```

改完后验证：
```bash
python -c "from backend.main import app; print('OK')"
```

---

### 1c. `arena/self_improve.py` — BUG 18：`IterationCallback` 不在 `__all__` 中

**位置**：文件末尾 `__all__` 元组。

**修复**：在 `__all__` 中加入 `"IterationCallback"`。

---

## 层 2：前端类型与状态修复（2 个文件）

### 2a. `frontend/src/api/index.ts` — BUG 1,2,3 — 接口字段错乱

#### 需要重建 `ArenaStatus` 接口（替换第 89-94 行）

当前：
```ts
export interface ArenaStatus {
  running: boolean;
  current_phase: string | null;
  progress: Record<string, unknown>;
  phases: Record<string, string>;
}
```

改为（完全匹配后端返回）：
```ts
export interface ArenaStatus {
  running: boolean;
  phase: string | null;
  domain: string | null;
  match_index: number;
  total_matches: number;
  latest_result: {
    domain?: string;
    skill_a?: string;
    skill_b?: string;
    winner?: string;
    score_a?: number;
    score_b?: number;
    elo_a?: number;
    elo_b?: number;
  } | null;
  current_battle: {
    skill_a?: string;
    skill_b?: string;
    domain?: string;
    match_id?: string;
  } | null;
  elo_snapshot: Record<string, number>;
  current_run_file: string | null;
  phases: Record<string, string>;
}
```

并在 `arenaRun` 中添加 `auto_categories` 和 `auto_per_category` 参数支持（第 118-125 行）：

```ts
  arenaRun: (opts?: {
    skills?: string[];
    task_source?: string;
    rounds_per_pair?: number;
    max_improve_iterations?: number;
    run_fusion?: boolean;
    run_improvement?: boolean;
    auto_categories?: string[];
    auto_per_category?: number;
  }) =>
    fetchAPI<{ status: string; run_id: string; event_file: string }>('/api/arena/run', {
      method: 'POST',
      body: JSON.stringify(opts || {}),
    }),
```

---

### 2b. `frontend/src/hooks/useArenaStatus.ts` — BUG 23, 31, 33

#### BUG 31：`loadReplay` 回放导致 N 次重渲染

**位置**：第 144-159 行。

**修复**：批量回调。先用一次 setEvents 写入全部事件，然后只对最后一条 match 事件更新 `liveBattle`/`latestResult`：

```ts
  const loadReplay = useCallback(async (filename: string) => {
    setIsReplaying(true);
    setEvents([]);
    setLiveBattle(null);
    setLatestResult(null);
    setReplayRunId(filename);
    try {
      const { events: hist } = await api.arenaRunEvents(filename);
      // 批量一次性设置
      setEvents(hist.slice(-200));
      // 只对最后一个 match 事件更新 UI
      for (let i = hist.length - 1; i >= 0; i--) {
        if (hist[i].type === 'phase_a_match') {
          handleEvent(hist[i]);
          break;
        }
      }
    } catch (err) {
      console.error('loadReplay failed', err);
    }
  }, [handleEvent]);
```

#### BUG 23：`skill_b: '...'` 在 `phase_a_skill_exec` 时

**位置**：第 88-91 行。

**修复**：改成 `skill_b: undefined`，前端用 `null` 判断：

```ts
    } else if (t === 'phase_a_skill_exec') {
      setLiveBattle({
        skill_a: evt.skill as string,
        domain: evt.domain as string,
      });
    }
```

#### BUG 33：`clearReplay` 立即拉状态

在第 168 行 `clearReplay` 最后加：

```ts
    api.arenaStatus().then(setStatus).catch(() => {});
```

---

## 层 3：前端组件修复（4 个文件）— 风格统一 + 逻辑修复

### 原则：所有组件从暗黑电竞风改为白底卡片风

映射：

| 暗黑风格 | 白底风格 |
|----------|----------|
| `bg-[#0a0e14]` 或 `bg-[#050810]` | `bg-white` |
| `border-[#1f2937]` | `border-gray-200` |
| `text-[#00ff88]` 标题 | `text-gray-900 font-semibold` |
| `text-[#00ff88]` 状态 | `text-green-600 bg-green-50` |
| `text-[#ff3366]` | `text-red-600 bg-red-50` |
| `text-[#ff8800]` | `text-amber-600 bg-amber-50` |
| `text-white` / `text-[#d1d5db]` | `text-gray-700` / `text-gray-500` |
| `font-mono` | 移除 |
| `tracking-widest` | 移除 |
| `[ BRACKET HEADERS ]` | 普通中文 `h2` |
| 回退线（circuit/gradient） | 移除 |
| `rounded` | `rounded-xl` |
| `shadow-[...]` / `animate-pulse` | `shadow-sm`（适当保留 `animate-pulse` 用于运行中状态） |

---

### 3a. `frontend/src/components/PhaseTimeline.tsx` — BUG 21：B/C 阶段钥匙不匹配

**关键修复**：`getPhaseStatus("B")` 需要检查 `B_writing`、`B_coding` 等领域化钥匙，而不只看 `"B"`。

替换 `getPhaseStatus` 函数：

```ts
  const getPhaseStatus = (key: string): 'done' | 'running' | 'pending' | 'failed' => {
    const flatStatus = status.phases?.[key];
    if (flatStatus && flatStatus !== 'pending') {
      return flatStatus === 'done' ? 'done' : flatStatus === 'failed' ? 'failed' : 'running';
    }
    // 对于 B/C：检查所有领域化钥匙 B_writing B_coding 等
    const prefix = key + '_';
    let hasRunning = false;
    for (const [pkey, pval] of Object.entries(status.phases || {})) {
      if (pkey.startsWith(prefix)) {
        if (pval === 'done') return 'done';
        if (pval === 'running' || pval === 'done') hasRunning = true;
        if (pval === 'failed') return 'failed';
      }
    }
    if (hasRunning) return 'running';
    // 如果 B/C 在 `run_full_cycle` 里正在跑，按当前的 phase 状态来判断
    if (key === phase) return 'running';
    return 'pending';
  };
```

**风格重写**：把暗黑风全部替换为白底风，参考 Leaderboard 页面。具体：
- 外层：`bg-white rounded-xl border border-gray-200 shadow-sm p-5`
- 移除所有 `tracking-widest`、`font-mono`
- 状态指示圆点保留颜色，移除阴影效果（`shadow-[0_0_20px...]`）
- 运行中状态保留 `animate-pulse`
- 标题改用普通 `h2`

---

### 3b. `frontend/src/components/BattleArena.tsx` — 风格统一

全部替换为白底风：
- 外层：`bg-white rounded-xl border border-gray-200 shadow-sm p-5`
- 选手卡片：`bg-gray-50 rounded-lg p-4`
- 胜者卡片：`border-green-300 bg-green-50`
- 对战中进行中：`border-blue-300 bg-blue-50`
- 进度条：`bg-green-500`（非渐变无阴影）
- 分数数字：`text-3xl font-bold text-gray-900`
- VS 文本：`text-gray-400 text-xl font-bold`

FighterCard 内 `text-[#00ff88]` 替换：
- 获胜者名字：`text-green-700 font-semibold`
- 标签：`text-xs text-gray-500`

---

### 3c. `frontend/src/components/EloLeaderboard.tsx` — BUG 26 + 风格统一

#### BUG 26：平分不稳定的排序

**位置**：第 12 行排序。

**修复**：

```ts
    .sort(([na, a], [nb, b]) => b - a || na.localeCompare(nb));
```

#### 风格重写：
- 外层：`bg-white rounded-xl border border-gray-200 shadow-sm p-5`
- 排名列：前 3 用 👑🥈🥉 emoji 或 `text-amber-500`
- 技能名：`text-gray-900` 不对等宽
- 能力条：`bg-blue-500`（非 `#00ff88`）
- 数值列：`text-gray-900 font-semibold`
- 变化量：绿 `text-green-600`/红 `text-red-600` 保留

---

### 3d. `frontend/src/components/EventLog.tsx` — BUG 28, 29, 30 + 风格统一

#### BUG 28：自动滚动不尊重用户

**位置**：第 97-101 行。

**修复**：

```ts
  useEffect(() => {
    if (ref.current) {
      const { scrollTop, scrollHeight, clientHeight } = ref.current;
      // 只在用户接近底部时才自动滚动（50px 阈值）
      if (scrollHeight - scrollTop - clientHeight < 50) {
        ref.current.scrollTop = scrollHeight;
      }
    }
  }, [events]);
```

#### BUG 29：`toFixed()` 对 undefined 崩溃

**位置**：第 41、64 行。

**修复**：在 `formatEvent` 中所有 `.toFixed()` 前加 `?? 0`：

```ts
    case 'phase_a_match':
      fields.push(`MATCH ${evt.match_id}`);
      fields.push(`${evt.skill_a} vs ${evt.skill_b}`);
      fields.push(`→ ${evt.winner}`);
      fields.push(`${(evt.score_a as number ?? 0).toFixed(1)}:${(evt.score_b as number ?? 0).toFixed(1)}`);
      break;
```

```ts
    case 'phase_c_iteration':
      fields.push(`ITER ${evt.iteration} ${evt.skill} Elo ${(evt.elo_after as number ?? 0).toFixed(0)} (Δ ${(evt.elo_delta as number ?? 0).toFixed(1)})`);
      break;
```

#### BUG 30：缺失 8 种事件类型的友好字符串

在 `formatEvent` 中加 `case` 分支：

```ts
    case 'cycle_start':
      fields.push(`CYCLE START skills=${evt.skill_count} tasks=${evt.task_count}`);
      break;
    case 'phase_a_domain_skip':
      fields.push(`DOMAIN ${evt.domain} SKIP (skills=${evt.skill_count})`);
      break;
    case 'phase_b_skip':
    case 'phase_c_skip':
      fields.push(`PHASE ${evt.type.includes('b') ? 'B' : 'C'} SKIP ${evt.domain}: ${evt.reason}`);
      break;
    case 'phase_c_skip_cached':
      fields.push(`PHASE C SKIP CACHED ${evt.skill}`);
      break;
    case 'phase_b_fuse_failed':
      fields.push(`FUSE FAILED ${evt.domain}: ${evt.error}`);
      break;
    case 'phase_c_improve_start':
      fields.push(`IMPROVE START ${evt.skill} max_iters=${evt.max_iterations}`);
      break;
```

#### 风格重写：
- 外层：`bg-white rounded-xl border border-gray-200 shadow-sm p-4`
- 日志区域：`bg-gray-900 rounded-lg`（日志区域保留暗底以提高可读性）
- 标题：`text-gray-900 font-semibold`
- 日志文字颜色：信息 `text-green-400`、警告 `text-amber-400`、错误 `text-red-400`、普通 `text-gray-300`
- 时间戳：`text-gray-500`

---

## 层 4：前端页面修复（1 个文件）— Arena.tsx 全面重写

### 需要做的事：

1. **移除暗黑风**，改为白底卡片
2. **移除 `-m-6 p-6`**（BUG 修复：布局溢出）
3. **用正确的类型** 替代 `(status as any)`（BUG 1,2）
4. **补全控制面板**：加 skill 多选、max_improve_iterations、run_fusion/run_improvement 开关、auto_categories/auto_per_category
5. **补全 `handleRun`** 参数：所有 RunRequest 字段

具体实现：

### 状态新增

```ts
  const [skills, setSkills] = useState<string[]>([]);
  const [allSkills, setAllSkills] = useState<SkillInfo[]>([]);
  const [maxIterations, setMaxIterations] = useState(2);
  const [runFusion, setRunFusion] = useState(true);
  const [runImprovement, setRunImprovement] = useState(true);
  const [autoCategories, setAutoCategories] = useState<string[]>(["writing", "coding", "analysis"]);
  const [autoPerCategory, setAutoPerCategory] = useState(3);
```

### 加载可用 skill 列表

在 useEffect 中添加：
```ts
  useEffect(() => {
    api.skills().then(setAllSkills).catch(() => {});
  }, []);
```

### handleRun 补全

```ts
  const handleRun = async () => {
    setRunning(true);
    setError('');
    try {
      await api.arenaRun({
        skills: skills.length > 0 ? skills.map(s => `skills/${s}.md`) : undefined,
        task_source: taskSource,
        rounds_per_pair: rounds,
        max_improve_iterations: maxIterations,
        run_fusion: runFusion,
        run_improvement: runImprovement,
        auto_categories: taskSource !== 'fixed' ? autoCategories : undefined,
        auto_per_category: taskSource !== 'fixed' ? autoPerCategory : 3,
      });
      setTimeout(() => api.arenaRuns().then(setRuns).catch(() => {}), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : '启动失败');
    } finally {
      setTimeout(() => setRunning(false), 2000);
    }
  };
```

### 类型读取（替换 66-70 行的 `as any`）

```ts
  const matchIndex = status.match_index ?? 0;
  const totalMatches = status.total_matches ?? 0;
  const eloSnapshot = status.elo_snapshot ?? {};
  const currentPhase = status.phase ?? null;
  const currentDomain = status.domain ?? null;
```

### 样式：从暗黑转为白底

- 最外层：`<div className="space-y-6">`（和其他页面一致）
- 移除 `min-h-screen bg-[#050810] -m-6 p-6` 和 `max-w-[1400px] mx-auto`
- 标题：`<h1 className="text-2xl font-bold text-gray-900">竞技控制台</h1>`
- 历史面板：`bg-white rounded-xl border border-gray-200 p-4`
- 历史文件按钮：`bg-gray-50 border-gray-200` 非暗色
- 连接状态指示器：保留在线/离线颜色但不替换等宽字体
- 所有 `bg-[#0a0e14]` → `bg-white`，`border-[#1f2937]` → `border-gray-200`
- 按钮：主按钮 `bg-blue-600 text-white`，重置按钮 `bg-red-50 text-red-600`

### 控制面板布局

```
┌─ 控制面板 ──────────────────────┐
│ 参与技能 (可多选，留空=全部)      │
│ [多选下拉或checkbox网格]         │
│                                 │
│ 任务来源  [fixed ▼]             │
│ 每对轮数  [2]                   │
│ 改进迭代  [2]                   │
│                                 │
│ (仅 auto/hybrid 显示)            │
│ 自动类目  [writing] [coding]... │
│ 每类目数  [3]                   │
│                                 │
│ [✓] 阶段 B 融合   [✓] 阶段 C 改进│
│                                 │
│ [▶ 完整运行 A→D]                │
│ [↺ 重置状态]                    │
└─────────────────────────────────┘
```

---

## 层 5：验证

### 5a. 全部测试

```bash
python -m pytest --tb=short -q
# 期望：154 passed, 1 skipped
```

### 5b. 前端构建

```bash
cd frontend && npm run build
# 期望：无类型错误，构建成功
```

### 5c. 端到端验证

```bash
# 终端 1
python -m uvicorn backend.main:app --port 8000

# 终端 2
cd frontend && npm run dev

# 浏览器打开 http://localhost:5173
# 1. 选择"竞技控制台"
# 2. 检查风格是否和其他页面一致（白底卡片）
# 3. 检查控制面板是否有 skill 多选 + 更多选项
# 4. 检查历史面板是否正常显示
# 5. 点"▶ 完整运行" 检查时间线是否显示正确
# 6. 检查 B/C 阶段是否在时间线中显示（不再永远 PENDING）
```

---

## 文件改动清单

| 文件 | 改哪些 | 影响的 bug |
|------|--------|-----------|
| `arena/orchestrator.py` | 预计算公公式 | BUG 13 |
| `arena/self_improve.py` | `__all__` 加 `IterationCallback` | BUG 18 |
| `backend/routers/arena.py` | RunRequest 模型+线程安全+状态处理器 | BUG 6,7,8,9 |
| `frontend/src/api/index.ts` | ArenaStatus 类型+arenaRun 参数 | BUG 1,2,3 |
| `frontend/src/hooks/useArenaStatus.ts` | 回放性能+`...`修复 + 清除时拉状态 | BUG 23,31,33 |
| `frontend/src/components/PhaseTimeline.tsx` | 风格+钥匙匹配 | BUG 21 |
| `frontend/src/components/BattleArena.tsx` | 风格 | — |
| `frontend/src/components/EloLeaderboard.tsx` | 风格+排序稳定性 | BUG 26 |
| `frontend/src/components/EventLog.tsx` | 风格+滚动+toFixed+友好字符串 | BUG 28,29,30 |
| `frontend/src/pages/Arena.tsx` | 完全重写 | BUG 34 |

---

## 实施顺序

```
层 1 (后端) → 验证 → 层 2 (类型+状态) → 验证 → 层 3 (组件) → 验证 → 层 4 (页面) → 层 5 (验证)
```

每完成一层就跑 `npm run build` 验证前端状态良好。
