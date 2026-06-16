# SkillArena · 任务与进度

> 最近更新: 2026-06-16

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

### 体验优化
- [ ] **减少竞技场数**:`RunRequest` + 前端加 `max_tasks_per_domain` 旋钮(默认 3);`rounds_per_pair` 默认改 1。当前 5 skill × writing = 150 场,目标降到 ~30-45 场
- [ ] *(可选)* baseline 开关:`run_full_cycle` 加 `include_baseline`,去掉裸 prompt 对手可省 C(n,2)→C(n+1,2) 的配对
- [ ] *(可选)* `skillOutputs` 流式输出也做刷新回灌(当前只回灌 events/对战态)

### 健壮性
- [ ] 生产环境实际跑通一次完整 A→B→C→D 全链路(本会话均为 mock/单测验证)
- [ ] 多专用领域 skill(如 `[writing, analysis]`)的兼容处理——当前会被判为 `hasDomainConflict`,可考虑"交集兼容域"模型

---

## 附:本轮一并提交的上一会话工作
- `arena/deepseek_client.py`:新增流式执行(`execute_stream`)支持
- `backend/routers/skills.py` + `frontend/src/pages/Skills.tsx` + `SkillEditor.tsx`:技能编辑器 UI
- skill 集裁剪:删除 `collected-*` / `gen-*` 旧 skill,新增 `coding-engineer.md` / `structured-output.md`
