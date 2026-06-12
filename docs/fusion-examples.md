# Skill 融合示例

> 本文展示两个 `fuse_skills` 真实使用场景的预期产出。
> 以下所有 v3 文本都是"合理的预期结果",不需要真实跑 API。

---

## 示例 1 · concise-writer × detailed-writer → hybrid-writer

### 任务上下文

通用技术写作任务,目标:清晰、有结构、不啰嗦,既不像简报那样信息不足,
也不像长文那样堆砌冗余。

### Skill A · concise-writer(简洁)

**强项**:第一句话就点明观点;短句优先;删掉冗词;结尾收束。
**弱项**:信息密度高时容易"骨架感太强",缺例子;读起来偏冷。

### Skill B · detailed-writer(详细)

**强项**:论点配具体例子;多角度展开;层次推进结构(是什么→为什么→怎么做→注意事项)。
**弱项**:默认铺陈过多,有时 50 字能说清的事写 200 字;开头铺垫拖沓。

### 评判反馈(节选)

- A 在 clarity 上 9.2/10,B 在 completeness 上 8.8/10,A 在 creativity 上 7.0/10。
- A 缺乏具体例子(典型弱点);B 段落冗长(典型弱点)。

### 融合后的 v3 · hybrid-writer

```markdown
# Hybrid Writer · 清晰技术写作风格

## 核心原则

1. **开门见山**:第一句话点明观点或结论,先给"是什么"。
2. **论点必有例**:每提出一个论点,至少配一个具体例子(数字、场景或引语)。
3. **短句+层次**:短句优先;用"是什么 → 为什么 → 怎么做 → 注意事项"四段式推进。
4. **删冗不删肉**:删掉"非常""一般来说"等填充词,但保留必要的解释和例子。
5. **多角度收尾**:结尾给"利弊"或"对比"小段,1-2 句即可。

## 行为约束

- 禁止"在当今社会""随着时代发展"等套话开头。
- 禁止"比如某些情况下"这种空泛例子 —— 例子必须具体到场景或数字。
- 禁止反复复述同一论点;每段只表达一个新想法。

## 示例

输入:解释什么是缓存,3 段以内。
输出:
缓存是把"算过的东西"存起来,下次直接用,避免重复劳动。
例如浏览器缓存:你第一次访问某 CSS 文件,浏览器把它存到本地;
下次再访问同一页面时直接读本地,不再向服务器请求,首屏时间从 800ms 降到 200ms。
但缓存不是越久越好 —— 数据更新后,旧缓存会变成"假数据",所以通常需要加过期时间。
```

### 融合效果点评

- 保留了 A 的"开门见山 + 短句 + 删冗词"。
- 加入了 B 的"论点配例子"和"四段式层次推进"。
- 显式回避了 A 的"骨架感"(加了 5 条原则,而非 3 条)和 B 的"铺陈过多"(明确"3 段以内")。

---

## 示例 2 · structured-writer × concise-writer → executive-brief

### 任务上下文

高管简报(Brief)场景:读者只给 60 秒,必须让他/她在 30 秒内抓到"是什么 + 该做什么",
对创造性、文学性要求为零,结构化和简洁度要求最高。

### Skill A · structured-writer(结构化)

**强项**:分点 + 表格化;明确 H2 层级;对比用表格;首尾呼应(导读+小结)。
**弱项**:有时把"可以用一句话说清的"也强行列表化,过度结构化反而冗长。

### Skill B · concise-writer(简洁)

**强项**:第一句话给结论;短句;删冗词。
**弱项**:缺结构,60 秒场景下读者扫不到要点。

### 评判反馈(节选)

- A 在 clarity 上 8.5/10,B 在 clarity 上 9.1/10;A 在 completeness 上 9.0/10,B 在 completeness 上 6.5/10。
- A 在某些场景下过度结构化(典型弱点);B 缺结构(典型弱点)。

### 融合后的 v3 · executive-brief

```markdown
# Executive Brief · 高管简报风格

## 核心原则

1. **结论第一**:第一句给"该做什么"或"我们的判断",不绕弯。
2. **三块结构**:全文只分三块 —— TL;DR(1 句话) / 关键事实(≤3 条 bullet) / 行动建议(≤3 条 bullet)。
3. **bullet 一句话**:每个 bullet 不超过 25 字,主动语态,能用数字时用数字。
4. **不列表化结论**:不要把"是否继续推进"这种一句话结论拆成 3 个 bullet。
5. **结尾留动作**:最后一条 bullet 必须是"谁、什么时候、做什么",而不是"建议进一步研究"。

## 行为约束

- 禁止"在评估了 X、Y、Z 之后,我们认为..."这种铺垫式开头。
- 禁止"建议进一步研究""未来值得探索"这种没有 owner 的虚结尾。
- 禁止超过 3 个 bullet 的列表 —— 真有 4 条就合并。

## 示例

输入:写一份关于"是否继续投入 A 项目"的简报。
输出:
**TL;DR**:继续 A 项目,Q3 目标是把 DAU 从 1.2 万推到 3 万。
**关键事实**:
- 当前 DAU 1.2 万,周留存 38%,7 日 ROI 1.4。
- 竞品 B 同期 DAU 0.8 万,留存 41%,但 ROI 0.9。
- A 项目下季度人力预算需 2 个工程师。
**行动建议**:
- 张三在 7/15 前完成 A/B 流量切换,目标 7 日 ROI ≥ 1.5。
- 李四在 7/30 前提交下季度预算拆解。
```

### 融合效果点评

- 保留了 A 的"分点 + bullet + 明确结构",但**收紧到三块**,避免过度结构化。
- 加入了 B 的"第一句给结论",让 TL;DR 块严格 1 句话。
- 显式回避了 A 的"无脑列表化"(原则 4) 和 B 的"缺结构"(强制三块)。

---

## 调用方式(节选自 `deliverable-fusion-engine.md`)

```python
from arena.fuse import fuse_skills
from arena.runner import load_skill

skill_a = load_skill("skills/concise-writer.md")
skill_b = load_skill("skills/detailed-writer.md")

v3 = fuse_skills(
    skill_a_content=skill_a,
    skill_a_name="concise-writer",
    skill_b_content=skill_b,
    skill_b_name="detailed-writer",
    task_context="通用技术写作,目标清晰有结构不啰嗦",
    judge_feedback="A 简洁但缺例子;B 详细但啰嗦",
    model="deepseek-v4-pro",
)

# 落盘
Path("skills/hybrid-writer.md").write_text(v3, encoding="utf-8")
```

CLI 形式:

```bash
python -m arena.cli fuse \
  --skill-a skills/concise-writer.md \
  --skill-b skills/detailed-writer.md \
  --task-context "通用技术写作" \
  --judge-feedback "A 简洁但缺例子;B 详细但啰嗦" \
  --output skills/hybrid-writer.md
```
