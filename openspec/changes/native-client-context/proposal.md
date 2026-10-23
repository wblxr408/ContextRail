# Proposal

## Why

A03/A04 已提供精确任务包与原文，但两端还缺绑定配置、原生 Hook 组合和实际客户端调用证据。继续 A06，并完成其使用所需的 A08 可启停布局，使源码、配置和既有 main 记忆职责清楚。

## What Changes

- 为可信宿主注册只读会话，MCP 增加指定 snapshot 的 context.packet 读取，仍不开放执行和 scope 重绑。
- 生成 Codex TOML/Hook 与 Claude MCP/settings 配置、单一源码 runner 和本机绑定注册表。
- 以 UserPromptSubmit 适配原生字段，复用现有记忆 Hook 命令一次组合历史与完整任务包；关闭 Rail 后透传原记忆结果。
- 任务证据更新后，从明确绑定的 Bridge 基快照按 owner 租约刷新运行时视图，不猜同项目最新源，不转移所有权。
- 两端实际客户端分别验收任务包和冷页；原生 Hook 信任审阅与模型渠道缺口明确记录，不能用 SDK 名称替代证据。

## Capabilities

### New Capabilities
- `native-client-context`: 本机客户端绑定、Hook 组合、只读任务包读取与独立启停。

### Modified Capabilities
无。

## Impact

沿用 Python/SQLite 与官方 MCP SDK，无新依赖/记忆库/监听器。先生成可审阅项目适配，个人客户端配置部署和原生信任保持宿主边界。A06/A08 只有满足各自完成标准才勾选，整体持续目标保持 active。
