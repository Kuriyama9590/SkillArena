# Source: https://prompts.chat/prompts/cmq5b1zzi0004la04htszgti1_shadcn-component-adapter-for-cursor
# Collected: 2026-06-12
# Category: coding
# Style: shadcn 视觉适配 — 7 步流水线,只动 UI 不动业务
# Original lang: en

## 目标
把现有 React 组件的视觉层对齐到 shadcn 参考组件,保留全部业务逻辑。

## 七步流水线

1. **审计现有组件**:读源码,列 props/state/context/hooks/子组件/imports
2. **依赖解析**:跑 `bunx --bun shadcn@latest add <name>` 拉取到 components/ui/
3. **解析参考组件**:抓 cva 变体、data-state、动画 class、ARIA、cn() 模式
4. **视觉重构**:把第 3 步视觉套到第 1 步逻辑上
5. **Context/Provider 挂载**:缺失的 Provider 加到 app/layout.tsx,给精确 diff
6. **澄清问题**:数据形态、状态管理、资产、响应式断点、放置位置
7. **输出三件套**:重构后组件 + shadcn 原始件 + utils 变更 + 迁移说明

## 硬规则
- props 名称与类型尽量保留,等价的 shadcn 才替换
- 业务逻辑、数据获取、回调一律不动
- forwardRef + spread props
- 用 cn() 合并 className,禁字符串拼接
- 严格 TS,只用项目已声明依赖,不擅自升降版本
