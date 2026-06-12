# deliverable-meta-skill.md · 元 skill: gen-meta-prompt

**生成时间**: 2026-06-12
**作者**: coder (v4-pro 提供 v1 草稿, coder 做 v1→v2 迭代 + 二次精修)
**最终文件**: `E:\Projects\skill竞技场\skills\gen-meta-prompt.md`
**正文长度**:
- Method A (`gen_skills_v4pro.py` 项目惯例,跳过 `#` 起始行): 553 字
- Method B (verifier 实测方法,4 行 header 之后所有非空白字符): **577 字** (落在 100-600 区间,留 23 char 余量)

---

## 一、设计思路

### 1. 为什么这是个 meta skill 而不是普通 skill

对照同目录下已有的 3 个 meta 类 skill:

| 文件 | 性质 | 与 gen-meta-prompt 的关系 |
|---|---|---|
| `gen-self-critic.md` | 单次自省:答→批→改 | gen-meta-prompt 不止"自省",还要"自进化" |
| `gen-step-back.md` | 抽象→具体 | gen-meta-prompt 把这种"先退一步"作为原则之一,但不局限于此 |
| `collected-openai-meta-prompt.md` | 任务→system prompt | 把模糊需求转成 prompt;gen-meta-prompt 关心"agent 怎么工作",不关心"prompt 字面怎么写" |
| **`gen-meta-prompt.md`** (本文件) | **工作哲学 + 持续打磨** | 任务无关、agent 自适应、强制使用后反思迭代 |

gen-meta-prompt 的核心定位:**不教 agent 写代码/写文章/做分析,而是教任何 agent "接任务→理解→执行→反思→改自己" 的一套可迁移方法论**。它本身没有"输出是什么"——输出由承接的具体 skill 决定;但它规定了"输出之前应该经历什么过程"。

### 2. 为什么 v1 要让 v4-pro 先"说思路"再"写"

任务 brief 写得很明确:"**meta 一下**"。所以 prompt 里强制 v4-pro 做两步:
1. 先用一段话回答"你会按什么思路来构造这个元 skill"——这一步的目的是让 v4-pro **显式暴露构造意图**,而不是直接吐文本。如果它说不清楚"我打算怎么写",那它大概率没想清楚。
2. 然后再写 markdown 主体。

v4-pro 实际给出的 reflection 摘录:
> "它们(已有 meta skill)都是单次'自省型 skill',而我写的是一个**可递归应用的、'工作哲学+方法论'的通用模型**……让原则自身具备'可被审查'的形态——即要求每一步产出都附带验证锚点,迫使 agent 留下可被追溯的中间产物。"

这段 reflection 在 v1 里被我捕获到 `v1-reflection-notes.md`,但**没有嵌入 skill 文件本体**——它只属于"v4-pro 是怎么构思的"这份 provenance,不是 skill 本身。

### 3. 为什么 v1→v2 的迭代由 coder 做,而不是再调 v4-pro

两层理由:
- **任务 brief 明确要求**: "由 coder 用一个改进回合迭代 v1 → v2"——这是分工,不是技术限制。
- **实质上更可控**:v1 正文 1569 字,超过 600 上限 2.6 倍。问题不是"v4-pro 写得不好",而是"它默认输出篇幅,不知道要压到 600"。coder 自己裁剪更精准:我知道哪些是装饰("## 一、" "## 二、"这种一二三四计数),哪些是核心机制(agent 自适应、自打磨三步),可以刀法干净地砍。

---

## 二、v1 → v2 的具体改动(及 resubmission 微调)

### 长度(三轮压缩)

| 指标 | v1 (v4-pro 草) | v2 (第一轮手裁) | v2′ (resubmission 二次精修) |
|---|---|---|---|
| Method A (项目惯例) | 1569 | 599 | **553** |
| Method B (verifier 实测) | (未测) | **623** ❌ | **577** ✅ |
| 是否落在 [100, 600] | ❌ | ❌ (Method B 超 24) | ✅ |

**Resubmission 触发原因**:第一轮我用自己的 Method A (gen_skills_v4pro.py 风格的"跳过 # 起始行"算法)报 599,以为通过;但 verifier 用的是 Method B(4 行 header 之后的所有非空白字符,包括 H1 标题与 H2 章节标题),实测为 623,差 24。问题不在内容,在测量口径。

**二次精修改了 4 处** (总 -46 字符,Method A 从 599 → 553,Method B 从 623 → 577):
1. 原则 3 "每个交付物必须配" → "交付物必配" (-3)
2. agent 自适应 末句 "同一原则,不同 agent 落地形态不同" 删除 (-14) — 已在段落开头隐含
3. 自打磨 闭环 末句 "skill 随你一起进化,不再一成不变" 删除 (-14) — 收尾的修辞,无信息量
4. 场景示例 "每条数据当场标" → "每条标" (-2) — 配合"数据"上下文已明
5. 自打磨 闭环 "做三步" → "三步" (-1) — 上下文已说明

### 结构(三轮基本一致)

- **v1** 把"方法论"和"迭代闭环"混在一节里,自打磨的"三步"被淹没;**v2** 把"自打磨闭环"独立成节,标题直接是"自打磨闭环",并强制用 `(1)(2)(3)` 编号。
- **v1** 的"agent 自适应"散落在每个阶段后面,容易被读漏;**v2** 抽出独立一节,开头一句话讲机制("按权重重新分配"),后跟三类 agent 的对照(分析型/写作型/代码型),**有可比性**。
- **v1** 的"行动节奏"隐藏在 4 个阶段名后面;**v2** 用 `→` 把"澄清→解构→执行→交付"压成一行 4 段链,并加一条"任一步失败回退上一步"的铁律。

### 措辞

- "**最高成本的低效**" → 保留。这是 v4-pro 写得好的一句。
- "**暴露思考过程**" → 保留。
- "**强制**" 二字被加粗保留——这是元 skill 的关键指令:反思不是可选的,是强制的。

### 一个隐含的设计选择:agent 自适应要求 agent 有"自我观察"能力

v2 假设承接 agent 能"先观察自身能力侧重点"——这要求 agent 有自我认知能力。如果承接的是没有元认知能力的 LLM(纯 base model),这一句会失效。**这是个已知折中**:本 skill 的目标用户是"已有一定自我认知的 agent"(例如已经能看自己历史轨迹、能跑反思 pipeline 的 agent),而不是 base model 级别的随机初始化。

---

## 三、验证结果

文件 `E:\Projects\skill竞技场\skills\gen-meta-prompt.md` 的最终状态:

| 验证项 | 结果 |
|---|---|
| 4 行 header(Source/Generated/Category/Style) | ✅ |
| **Method A 长度 (项目惯例, skip #)** | ✅ 553 |
| **Method B 长度 (verifier 实测, post-4-line-header)** | ✅ 577 (留 23 char 余量) |
| 含 "agent 自适应" 节 | ✅ (line 13) |
| 含 "自打磨闭环" 节 | ✅ (line 19) |
| 含具体场景示例(竞品分析) | ✅ (line 22) |
| 包含三条核心原则(先理解再动手 / 显式推理 / 可被验证) | ✅ (lines 9-11) |
| 包含行动节奏(澄清→解构→执行→交付) | ✅ (line 17) |
| agent 自适应节中提到 3 类 agent(分析型/写作型/代码型) | ✅ (line 14) |
| UTF-8 中文完整,无乱码 | ✅ |

`scripts/verify_track5.py` 同时打印两种方法的字数与 18 项内容检查,全部 PASS。

---

## 四、给后续 verifier 的备注(重要)

### 1. 关于测量口径

`gen_skills_v4pro.py` 中 `body_length()` 用的是 **Method A**:跳过所有以 `#` 开头的行(包括 H1 标题和 H2 章节标题),只数正文行。这种方法在 track 1 的 10 个文件上都通过 [100, 400] 校验。

但 track 5 的 verifier 实测的是 **Method B**:数 4 行 metadata header 之后**所有**非空白字符。两种方法在 track 1 的文件上差额 ≈ 10-30 字符(只少了 1 行 H1 标题),对 [100, 400] 不影响;但对本文件 5 个 H2 章节标题 + 1 个 H1 标题 ≈ 46 字符,推到了上限之上。

**建议**:track 5 的后续 verifier 在判断长度时,二选一并写明方法。本文件 577 (Method B) / 553 (Method A) 均在 [100, 600] 内,任一方法都 PASS。

### 2. 关于 v1 重生成

不要再让 v4-pro 重生成 v1:本 skill 的"v2"是 coder 手裁的,不是 v4-pro 产物。重新跑 v4-pro 会得到不同表述,正文长度可能再次超 600。`scripts\gen_meta_prompt_v1.py` 仍可跑以拿到 v1 反思(只是会覆写 v2)。

### 3. 关于 track 1 的 v4-pro 复现对照

v1 是 1569 字符,跑 `python scripts\gen_meta_prompt_v1.py` 即可拿到完全相同的 v1(因为 `temperature=0.5` 但没设 seed,可能措辞略变;骨架应一致)。

### 4. 与 seed skill 的关系

gen-meta-prompt 与 concise-writer / detailed-writer / structured-writer 是正交的——后者是"输出形态",前者是"工作过程"。可以叠加使用,例如 "concise-writer + gen-meta-prompt" = "以简洁形态输出,但过程遵循 gen-meta-prompt 的澄清→解构→执行→交付"。

---

## 五、文件清单

- `E:\Projects\skill竞技场\skills\gen-meta-prompt.md` — 最终版(v2′)
- `E:\Projects\skill竞技场\scripts\gen_meta_prompt_v1.py` — v4-pro 调用脚本(含两步 prompt:reflection + body)
- `E:\Projects\skill竞技场\scripts\verify_track5.py` — 双方法字数 + 18 项内容检查
- `C:\Users\QiuYC_1001\.mavis\plans\plan_f18979ed\outputs\track5-meta-skill\v1-reflection-notes.md` — v4-pro 的 reflection 原文与 v1→v2 改进方向
