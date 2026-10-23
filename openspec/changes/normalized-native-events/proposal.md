# Proposal

## Why

E01依赖的F01/F04已完成，但既有listener只保存完成回合的学习候选，用户提交后崩溃时最后请求丢失。工具调用、结果及最终回复也缺少分别可回读的证据。

## What Changes

- 从原监听器读取的Codex/Claude记录归一化user、assistant、tool call/result与生命周期，先保存完整处理后证据，再沿用原知识候选规则。
- 保留原生session/turn/message/call ID、来源角色和明确的来源时间状态，缺时间不伪造发生时间；不读取thinking/reasoning正文。
- 接入F01来源键与F04策略，和原游标同事务；提供本地只读事件页/精确回读。
- 新增可信Rail显式导入，按workspace及选定native session读取原M事件，保存独立版本证据，可经现有MCP get/search回读。

## Capabilities

### New Capabilities

- `native-event-evidence`: 原生会话事件归一化、可恢复持久化与任务证据导入。

### Modified Capabilities

无已归档相关主spec。

## Impact

继续使用原EngineeringMemoryListener/main/state.sqlite与Python/SQLite，不新增观察器、模型调用或长期知识权威。复用context-mode固定commit的工具别名映射，保留ELv2许可和本地修改声明。F02/F03/F08/E08/C06保留独立范围。
