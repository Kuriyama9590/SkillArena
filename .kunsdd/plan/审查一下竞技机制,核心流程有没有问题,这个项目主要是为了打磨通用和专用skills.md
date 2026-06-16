# 竞技机制审查报告：核心流程问题诊断

## 审查范围

对 `arena/orchestrator.py` 主编排器的 **阶段 A→B→C→D 全流程** 进行逐环节审查，重点关注：
1. 竞技配对机制是否正确（Elo 对比的公平性）
2. 领域分区是否合理（通用 vs 专用 skill 的对比能力）
3. 融合/自改进逻辑是否与竞技结果正确衔接
4. 存在的 bug 与设计风险

---

## 一、核心流程概览（当前实现）

```
加载 skills（含领域标签）
  → 加载/生成 tasks
  → 阶段 A：按领域分组 → 每个 task × 每个 skill 执行产物 → 两两配对（含 baseline）→ judge 盲评 → 更新 Elo
  → 阶段 B：每领域取 Top2 Elo → 融合 → 输出 v3
  → 阶段 C：每领域取 Bottom1 Elo → 自改进循环
  → 阶段 D：生成 Markdown 总报告
```

---

## 二、发现的问题（按严重程度排列）

### 🔴 严重（影响竞技公平性与结果正确性）

#### 问题 1：Elo 在同 domain 内逐 task 串行更新，匹配顺序影响最终排名

**位置**：`orchestrator.py` 第 747-770 行（`_phase_arena` 内部循环）

**症状**：假设 domain `writing` 有 skills [A, B, baseline]，tasks [t1, t2, t3]。当前的执行顺序是：
```
t1: A vs B → 更新 Elo → A vs baseline → 更新 Elo → B vs baseline → 更新 Elo
t2: A vs B → 更新 Elo（基于 t1 后的分数）→ ...
```
这意味着 **t1 上的对战结果会影响 t2 对战时双方 Elo 差距带来的预期分变化**。这是标准 Elo 在线更新的正确行为——但问题是：所有产物在循环开始前已经**一次性全部生成并缓存**，不存在"新信息逐步到达"的情形。此时更合理的做法是**按 task 全批量收集 verdict 后统一更新**（或至少每个 task 内的对战独立于其他 task 的 Elo 状态），否则：
- 同一对 skill 在 task 顺序不同时，最终 Elo 会不同
- 无法保证"所有对战在平等条件下评判"

**建议**：阶段 A 结束后统一计算 Elo（先收集所有 verdict，再一次性 `run_round`），或者至少在注释中明确这是有意为之的"在线 Elo"策略。

---

#### 问题 2：Phase B 和 Phase C 对多领域只保留最后一个领域的结果

**位置**：`orchestrator.py` 第 262-272 行（阶段 B）、第 285-298 行（阶段 C）

**症状**：
```python
# 阶段 B：对每个 domain 循环
for domain in TASK_DOMAINS:
    ...
    fp, fc = self._phase_fusion(...)
    if fused_path is None:      # ← 只保存第一个成功融合
        fused_path = fp
        fused_content = fc
```
同样，阶段 C 也只保留最后一个 domain 的 improvement 结果。这意味着：
- 如果 writing 和 coding 领域都成功融合，最终报告只展示第一个
- 如果 3 个领域都有 Bottom1，只改进最后一个

**建议**：改为 `dict[str, Path]` 存储每个领域的融合/改进结果，报告也按领域分段展示。

---

#### 问题 3：Baseline 被排除融合和改进，但过滤逻辑脆弱

**位置**：`orchestrator.py` 第 260、288 行

```python
non_baseline = {n: r for n, r in elo_dom.items() if not n.startswith("baseline")}
```

**风险**：如果用户有一个 skill 命名为 `baseline-enhanced.md`，它会被错误排除。应使用更精确的匹配——例如从 `skills` dict 中判断（baseline 不在 `skills` 中），而不是靠字符串前缀。

---

### 🟡 中等（影响通用/专用 skill 打磨目标）

#### 问题 4：领域分区完全依赖自动推断，用户无法显式控制

**位置**：`skill_metadata.py` — `parse_skill_domains()`

**症状**：skill 的领域标签通过 YAML front matter → 文件名关键词 → 内容关键词 → 兜底 `general` 四级推断。但当前 `skills/` 目录下的大量 skill 文件（如 `gen-meta-prompt.md`、`collected-openai-meta-prompt.md`、`gen-self-critic.md`）都没有 front matter，其文件名和内容也未必命中 `_FILENAME_KEYWORDS` 或 `_CONTENT_KEYWORDS` 中的关键词。结果大量 skill 会被归为 `general`，参与到所有领域的竞技中。

**对"打磨通用 vs 专用 skills"目标的影响**：
- 无法区分"这个 skill 在 writing 领域得分高是因为它确实是通用 skill，还是因为它碰巧被标为 writing"
- 用户无法强制一个 skill 只参与某个领域来测试其专用性

**建议**：
1. 为 `skills/` 下所有文件补充 YAML front matter
2. 或者 CLI 支持 `--skill-domains` 覆盖参数
3. 报告增加"领域迁移能力"指标：一个 general skill 在各领域的 Elo 方差

---

#### 问题 5：缺乏"通用 skill vs 专用 skill"的对比设计

**症状**：当前竞技机制只做"同一领域内所有 skill 两两对比"。这意味着：
- 一个通用 skill（标为 `general`）和一个专用 skill（标为 `coding`）会在 coding 领域对比
- 但系统不会告诉你"通用 skill 在 coding 领域是否比在 writing 领域表现更差"
- 即缺少**跨领域迁移能力**的量化指标

**对打磨目标的影响**：这正是项目核心目标——打磨通用和专用 skill——所需的关键分析维度。

**建议**：在报告中增加一个"跨领域表现矩阵"：行=skill，列=domain，值=Elo，让用户一眼看出"这个 skill 是通用强还是专用强"。

---

#### 问题 6：Judge 的 winner 判定忽略分数差量级，Elo 更新丢失信息

**位置**：`judge.py` `Verdict.to_score()` → `elo.py update_rating()`

```python
def to_score(self) -> float:
    if self.winner == "A": return 1.0
    if self.winner == "B": return 0.0
    return 0.5
```

**症状**：一场 10-2 的碾压和一场 6-5 的险胜，对 Elo 的贡献完全相同。Elo 标准公式确实只需要 win/loss/draw，但 judge 模型已经给出了精细的维度分数（correctness/completeness/clarity/creativity 每维 0-10），这些信号被丢弃了。

**建议**：考虑引入基于总分差的 K 因子调整（总分差大 → K 稍大），或至少在报告中展示"平均分差"与"Elo 差"的相关性。

---

### 🟢 轻微（影响鲁棒性与可维护性）

#### 问题 7：`_top_k_skills` 和 `_bottom_skill` 在 Elo 平局时行为不确定

**位置**：`orchestrator.py`（需确认具体实现）

如果多个 skill 有相同的 Elo 分数，top-k 的选择依赖 dict 迭代顺序（Python 3.7+ 是插入顺序），导致不可复现。

**建议**：tie-breaking 规则（如按名称字母序、或按胜率）。

---

#### 问题 8：阶段 A 的产物缓存键只用了 `task_id` + `skill_name`，没有 hash skill 内容

**位置**：`orchestrator.py` `_cache_path()` 方法

**症状**：如果用户修改了 skill 文件内容但保留了相同文件名，再次运行时会命中旧缓存，导致用旧 skill 产物参与新对比。

**建议**：缓存键中加入 skill 内容的 hash（如 `md5`），或在 state 中记录 skill 文件 mtime。

---

#### 问题 9：FIX_PLAN.md 仍存在，修复状态不明

`FIX_PLAN.md` 文件未被删除（文件头写"修复完成后删除本文件"），但部分 bug（BUG 6/7/13）的修复已出现在代码中。**需要确认所有 20+ 项是否均已完成**，否则未修复的 bug 可能影响生产。

---

#### 问题 10：`_phase_arena` 中 `domain_total_matches` 计算漏乘 tasks 数

**位置**：`orchestrator.py` 第 729 行（emit 中的 `domain_total_matches`）

```python
domain_total_matches = len(domain_pairs) * rounds_per_pair  # ← 只算了 pairs
```

但在第 686-694 行的 `total_expected_matches` 预计算中已包含 `len(_dt)`。**emit 给前端的 `domain_total_matches` 与实际总场次不一致**，导致前端进度条在该领域内会超过 100%。

---

## 三、架构亮点（保持不动）

以下设计是正确的，不应修改：

1. **匿名评判协议**：judge 只看到 Response A/B，不泄露 skill 名——这是 Elo 对比公平性的基础 ✅
2. **Baseline（无 skill 裸 prompt）的设计**：作为对照组的思路正确，且被正确排除在融合/改进之外 ✅
3. **断点续跑 + 产物缓存**：设计合理，大幅降低 API 成本 ✅
4. **分领域 Elo 状态持久化**：JSON 格式清晰，兼容扁平/分领域两种格式 ✅
5. **重试机制**：judge/fuse/improve 均有一次修复重试，覆盖率合理 ✅
6. **Mock 客户端测试架构**：`_FakeDeepSeekClient` 按 prompt 内容路由的设计精巧，154 个用例覆盖充分 ✅

---

## 四、优先级修复顺序

| 优先级 | 问题 | 影响 | 修复复杂度 |
|--------|------|------|-----------|
| P0 | 问题 2：B/C 阶段只保留最后领域结果 | 多领域数据丢失 | 低 |
| P0 | 问题 10：domain_total_matches 漏乘 tasks | 前端进度条 bug | 低 |
| P1 | 问题 1：Elo 在线更新顺序依赖 | 排名稳定性的理论风险 | 中 |
| P1 | 问题 3：baseline 过滤逻辑脆弱 | 边缘 case 的健壮性 | 低 |
| P1 | 问题 4：领域推断不可控 | 打磨通用/专用的核心能力 | 中 |
| P2 | 问题 5：缺少跨领域迁移分析 | 打磨目标的量化缺失 | 高 |
| P2 | 问题 6：Judge 分数未被 Elo 利用 | 信号丢失 | 中 |
| P3 | 问题 7/8：缓存失效/Tie-breaking | 鲁棒性 | 低 |
| P3 | 问题 9：FIX_PLAN 清理 | 项目管理 | 低 |

---

## 五、总结

核心竞技闭环（加载→竞技→评判→Elo→融合→改进→报告）在**单领域场景**下逻辑正确、测试覆盖充分。两个最关键的短板都直接关系到项目目标"打磨通用和专用 skills"：

1. **多领域结果被覆盖**（问题 2）：当前跑 3 个领域只会产出 1 个领域的融合和改进产物
2. **缺少跨领域对比能力**（问题 5）：系统能告诉你 skill A 在 writing 领域排第几，但不能告诉你 skill A 是"通用强"还是"专用强"

修复 P0/P1 问题后，这个竞技场将是打磨 skill 的有力工具。
