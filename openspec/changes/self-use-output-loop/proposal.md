# Proposal

## Why

当前 Rail 有证据、快照和只读工具，但尚无项目配置、真实 MCP 与工具结果采集入口。用户明确选择自用 TODO 的 A01、A02、A04、A05、B01、B02、B05、B07，要求把大输出存为证据并按引用回读。

## What Changes

- 增加项目 TOML 配置、仓库/worktree/task/session 身份映射及临时覆盖。
- 使用官方 Python MCP SDK 提供单 scope/session 的只读 stdio adapter。
- 增加有来源和保守降级的宿主能力报告。
- 接收宿主已执行结果，原子保存 stdout/stderr/状态及有界 JSON 展示；按明确工具语义区分无匹配与失败。
- 增加本批行为的针对性测试、使用指南并更新选定 TODO 的完成证据。

## Capabilities

### New Capabilities

- `local-project-context`: 项目配置与本机仓库/worktree 身份。
- `tool-result-evidence`: 工具结果保存、分类、有界展示与原文回读。
- `mcp-context-access`: 真实 MCP 只读访问和宿主能力报告。

### Modified Capabilities

无既有 OpenSpec capability 需要变更；现有核心接口保持兼容。

## Impact

核心继续使用标准库 Python/SQLite。MCP adapter 以可选 `mcp` extra 安装官方 SDK，不手写协议或引入另一框架。变更仅在本项目内，不写客户端个人配置、不建立新的记忆后台。仓库已有未提交研究材料保持原状。
