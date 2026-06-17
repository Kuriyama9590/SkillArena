# SkillArena · 任务与进度

> 最近更新: 2026-06-17

---

## ★ 2026-06-17 grill 进度（需求文档定稿轮）

本轮做了需求文档的 grill + 落盘，未动业务代码。下次会话从这里续。

### 已产出文档
- `docs/REQUIREMENTS.md` —— **当前唯一需求基线**（取代 README 旧 CLI 定位），13 节，已审查（haiku 事实核对 + 自审，修正路径引用与 5 处歧义）
- `README.md` —— 重写，Web 优先，砍掉 CLI/库 API，指向 REQUIREMENTS.md
- `CLAUDE.md` —— 新建，架构 + 命令 + 子代理使用指南
- 本 todo.md —— 赛道重构/产物入榜/劣化记忆库/软删除等章节已更新

### 本轮已拍板的需求结论（详见 REQUIREMENTS.md）
- 交付形态：Web 为主，CLI 废弃
- 赛道：6 条（代码/写作/推理/角色扮演/指令遵循/长文本）+ general 跨域属性；判据=LLM 能力分化大
- 排名：无跨赛道总榜，首页 6 冠军展示墙；分赛道榜；general 可隐藏；baseline 必现且争冠
- Elo：K=32/1500，分赛道分仓，只由 winner 驱动（维度分不进 Elo）
- 选手：专用 skill 严格一对一；general 通吃 6 赛道各独立 Elo；baseline=裸 prompt
- 产物：独立新 skill 入榜，标血缘，只留 3 类冠军形态；统一过定级赛
- 入榜门槛：超越原始版本（融合超较强父代 max(A,B)）
- 劣化产物：不入榜但重点归档（存父代/结果/评语），作下次改进负样本
- 自改进：达上限未超越父代→彻底放弃归档（不降级入榜不重试）
- 删 skill：软删除 + 血缘快照，Elo 通缩不管
- 状态：所有回放可看、刷新维持、Elo 跨 run 累积；加新 skill 不重跑历史
- general 定级赛：6 条赛道各打（准确性优先）

### 下次 grill 续接点（按优先级）
- [ ] **【最高】general 在某赛道未开榜时怎么定级**：a=vs baseline 定级接受虚高分 / b=待开榜后定级（推 b，与动态开榜精神一致）。本轮未拍，**这是下次第一个要答的**
- [ ] general 定级赛成本确认：≈27 次 API 调用/general 入榜，reasoner 翻倍——接受？（倾向接受，准确性优先）
- [ ] general Elo 高频变动是否前端标注（倾向不标注，是 general 本质）
- [ ] 产物"3 类冠军"的"最好"判定口径（定级 Elo？后续胜率？超越幅度？）
- [ ] 劣化记忆库结构与检索（按赛道？血缘？注入格式）——新组件，全待设计
- [ ] 评判维度是否按赛道调整（角色扮演加"人格一致性"？指令遵循部分机检取代盲评？）
- [ ] 定级赛场次稳定性（1 场偶然 vs 2-3 场）与对手选择
- [ ] 首页 6 冠军展示墙信息架构（展示哪些字段？点击进分赛道榜？）

---

## 本轮已完成


### Skill 领域隔离(核心)
- [x] **严格标记**:`arena/skill_metadata.py` 移除 `general` 兜底,无 front matter / 无法推断的 skill 加载失败(抛 ValueError);`backend/deps.py` 不再伪造 `general`
- [x] **前端跨域防护**:`frontend/src/pages/Arena.tsx` 控制面板锁定领域——选中一个专用领域后其余灰显禁用,`general` 始终可选;含冲突检测 + Run 按钮守卫
- [x] **后端跨域校验**:`backend/routers/arena.py` 新增 `_validate_skill_domains()`,`arena_run` 拒绝跨专用领域的 skill 选择
- [x] **编排器锚定隔离**:`arena/orchestrator.py` 新增 `_domain_is_active()`——存在专用 skill 时,某 domain 必须有专用 skill 锚定,`general` skill 不能独自开竞技场(修复 "coding 技能参与 writing/analysis")
- [x] **auto 类目联动**:`Arena.tsx` 的 `effectiveAutoCategories` 锁定后强制为对应领域(修复 "选 coding 技能仍能勾 writing 类目")
- [x] **matches.jsonl 补 domain**:`orchestrator.py` 的 `_serialize_match` / `_deserialize_match` 加 `domain` 字段(旧数据全是 None)

### 竞技状态持久化
- [x] **前端回灌**:`frontend/src/hooks/useArenaStatus.ts` 挂载时读 `current_run_file` 恢复 `events` / `liveBattle` / `latestResult`;`run_start` 清场
- [x] **后端重建**:`backend/routers/arena.py` 新增 `_reconstruct_status_from_disk()`,模块加载时从最近一次运行的事件文件 + `elo_state.json` 重建 `_active_status`(`running` 强制 false)——进程重启后状态保留
- [x] **修复白屏**:`useArenaStatus.ts` hydration effect 原本依赖 `handleEvent` 却写在它声明之前(TDZ),已移到声明之后

### 验证
- [x] 154 测试全过(1 skipped e2e);前端 `tsc --noEmit` + `vite build` 通过
- [x] 后端重启模拟:status 完整重建(current_run_file / match_index / elo / latest_result)

---

## 剩余 TODO

### ★ 赛道模型重构（2026-06-17 grill 结论，优先级最高）

> 旧领域模型 `writing/coding/analysis` 废弃，整套替换为下述能力赛道模型。

**赛道定义（6 条专用 + 1 跨域属性）—— 2026-06-17 终稿**

选赛道判据：只开"当前 LLM 能力分化大"的领域（信号优先），不做"能力图谱铺满"。凡是现代 LLM 普遍做得好的（翻译/普通总结/基础改写）一律不开。

| # | 赛道 | 边界 | 信号 | 自养 |
|---|---|---|---|---|
| 1 | 代码 | 可执行代码/技术方案 | 强 | 易 |
| 2 | 写作 | 原创表达（含文学翻译这类再创作） | 强 | 易 |
| 3 | 推理 | 分析/综合/多步推导（无明确标准答案） | 最强 | 中 |
| 4 | 角色扮演 | 扮演特定身份/人格完成任务 | 中强 | 中（任务难造）|
| 5 | 指令遵循 | 复杂多约束指令全满足（含结构化输出/schema 约束） | 强 | 易（评判可机检：约束满足数）|
| 6 | 长文本处理 | 超长输入下检索/定位/综合，"长"带来的遗忘/遗漏是核心考察点 | 强 | 中（素材用四大名著等公版长文本兜底）|

- `general`：跨域属性（非赛道），声明 general 的 skill 报名全部 6 条赛道
- 排除的候选及理由：翻译（LLM 普遍做得好，无信号）/ 总结（信号弱）/ 数学（与推理重叠，skill 调节空间小）/ 数据提取（与推理重叠）/ 改写润色（与写作重叠）/ 对话客服（任务难造）/ 创意故事（与写作重叠且评判主观）/ 结构化输出（并入指令遵循，为其子集）/ Agent（复杂且市面饱和）/ 审阅·教学（不要）

**核心规则**
- [ ] 赛道是独立维度，任务和 skill 都向赛道贴标签；同赛道才配对战（旧"任务 category 驱动"改为"赛道驱动"，任务创建时即归属某赛道）
- [ ] 非 general skill **严格一对一**：一个 skill 只属 1 条专用赛道
- [ ] general skill 在每条赛道都有独立 Elo 分，进每条赛道榜
- [ ] **无跨赛道总榜**；首页是 6 冠军展示墙（陈列各赛道分冠军，不可比，不跨赛道排序）
- [ ] general 每条赛道榜都出现，前端可勾选显示/隐藏
- [ ] baseline = 裸 prompt（不加任何 skill），通吃所有赛道，**必须出现且争冠**
- [ ] **动态开榜**：某专用赛道 ≥2 个专用 skill 才立该赛道榜，否则该域 skill 暂只与 general/baseline 比、不单独立榜（防空天梯）

**涉及改动**
- [ ] `arena/skill_metadata.py`：`VALID_DOMAINS`/`TASK_DOMAINS` 改为 6 赛道 + general；移除 writing/coding/analysis 旧词
- [ ] `tasks/fixed/*.yaml`：旧 3 类任务重新归类到 6 赛道（analysis→推理，逐条判断；其余赛道补题）
- [ ] `arena/orchestrator.py`：赛道驱动配对、分赛道 Elo（已有 `domain_elo`，需对齐新赛道词）
- [ ] 前端 `Skills.tsx`：按 6 赛道 + general 分组展示
- [ ] 前端导航：**取消 Leaderboard 页**，首页 Dashboard 即天梯（6 冠军展示墙 + 概览）；导航从 6 入口降为 5（Dashboard/Arena/Skills/Matches/Reports）

### ★ skill / 任务搜集（赛道立起来的前置条件）
- [ ] 每条专用赛道至少补齐 **2-3 个专用 skill**：代码、写作、推理、角色扮演、指令遵循、长文本各缺多少先盘点 `skills/` 现状
- [ ] 每条赛道补齐 **一批同域任务**（≥5 题/赛道，供 Elo 有足够样本）
- [ ] **长文本赛道素材**：抓取四大名著等公版长文本作为题目输入素材（素材是长文本赛道唯一难点，公版可解）
- [ ] general skill 是否需要单独 1-2 个（跨域基线用），还是仅靠 baseline 兜底
- [ ] baseline 选手实现确认：当前 `baseline` 虚拟选手是否=裸 prompt，与"不加任何 skill"语义对齐

### ★ 产物入榜与定级赛规则（2026-06-17 grill 结论）

> 适用对象：所有新 skill，含手动新增、融合产物、自改进产物。统一过定级赛。

- [ ] **定级赛机制**：新 skill 先打 1-2 场定级赛（vs baseline 或当前赛道中位选手），完成才正式入榜；未完成标"未入榜"
- [ ] **定级赛必须保存**：比赛结果 + 评委评语（评语是后续改进的输入信号，不可丢）
- [ ] **产物入榜门槛 = 超越原始版本**：
  - 改进产物 `v2=improve(A)`：定级 Elo 超越 A 才入榜，否则判劣化
  - 融合产物 `v3=fuse(A,B)`：定级 Elo 超越**较强父代** max(A,B) 才入榜（门槛最高，融合必须 1+1>2 才有意义）；否则判劣化
- [ ] **劣化产物处理**：不入榜，**重点归档**（不丢弃）。归档存：产物本身 + 父代血缘 + 定级赛结果 + 评委评语 + 劣化点摘要
- [ ] **血缘标注**：入榜产物在天梯上标注血缘（`fuse(A,B)` / `improve(A)` / `improve(fuse(A,B))`），只保留 3 类冠军产物形态：最好融合型 / 最好迭代改进型 / 最好融合+改进型（每次 run 各留该类冠军，累积）
- [ ] **自改进循环语义修正**：阶段 C 不再假设"改进必成功"。改进产物过定级赛，超越父代入榜，未超越归档为劣化。改进达 `max_improve_iterations` 上限仍未超越父代 → **彻底放弃 + 归档**（不降级入榜，不无限重试）
- [ ] **代码现状对照**：`run_improvement_cycle` 当前默认成功，需改为"定级赛判定"；产物当前落 `reports/improved/` 临时目录，需改为持久化归档 + 血缘结构

### ★ 劣化记忆库（新组件，待设计——先记 todo，以后展开）
- [ ] 设计持久化"劣化记忆库"：存所有劣化产物的评语，作为下次改进的负样本输入
- [ ] 待定：记忆保留多久（防膨胀）、检索方式（按赛道？按父代血缘？）、注入改进 prompt 的格式
- [ ] 实现位置：当前代码无此组件，需新建

### ★ 删 skill 处理（软删除）
- [ ] 删 skill 一律**软删除**（标 `deleted`/`inactive`），不物理删文件——保护历史回放、血缘链、可复现性
- [ ] 软删后：不参与新 run 配对，但天梯历史榜和回放仍可见（灰色标注"已停用"）
- [ ] **血缘保护**：软删时若该 skill 有后代在榜，后代血缘记录补存该 skill 内容快照，防血缘悬空
- [ ] Elo 通缩：不管（单 skill 进退对全局均值影响 <5 分，可忽略）

### 体验优化
- [ ] **减少竞技场数**:`RunRequest` + 前端加 `max_tasks_per_domain` 旋钮(默认 3);`rounds_per_pair` 默认改 1。当前 5 skill × writing = 150 场,目标降到 ~30-45 场
- [ ] *(可选)* baseline 开关:`run_full_cycle` 加 `include_baseline`,去掉裸 prompt 对手可省 C(n,2)→C(n+1,2) 的配对
- [x] ~~`skillOutputs` 流式输出也做刷新回灌~~ → **已完成**（`useArenaStatus.ts:113-122`，调研核实 2026-06-17）

### 健壮性
- [ ] 生产环境实际跑通一次完整 A→B→C→D 全链路(本会话均为 mock/单测验证)
- [ ] ~~多专用领域 skill(如 `[writing, analysis]`)的兼容处理~~ → **作废**：新模型严格一对一，不再支持多专用领域，`hasDomainConflict` 逻辑改为直接拒绝

---

## 附:本轮一并提交的上一会话工作
- `arena/deepseek_client.py`:新增流式执行(`execute_stream`)支持
- `backend/routers/skills.py` + `frontend/src/pages/Skills.tsx` + `SkillEditor.tsx`:技能编辑器 UI
- skill 集裁剪:删除 `collected-*` / `gen-*` 旧 skill,新增 `coding-engineer.md` / `structured-output.md`
  - ⚠️ 调研核实(2026-06-17):`gen-*` 4 个文件**实际未删**（gen-chain-of-thought/gen-code-reviewer/gen-devils-advocate/gen-self-critic 仍在 skills/，带 front matter 正常加载）。collected-* 确已删。待赛道重构时一并重新归类或清理。
