# Proposal

## Why

首批已能保存工具输出，但 Bridge 要求和 Rail 工作集仍分离。按持续实现 TODO 的授权，先完成 A03/D02，使最新目标、约束和验收随精确来源进入恢复上下文。

## What Changes

- 从已验证 Bridge bundle 单向导出完整账本、原文、逐条 ID/revision/status/scope/quote 和证据。
- 复用原 Bridge 校验器与逐条接收回执，保存不可变导出和 Rail 快照。
- 有效 goal/constraint/always_load 固定装入，旧 superseded 要求只作历史，超限明确列出必需 ID。
- 增加 bridge-import/bridge-packet/bridge-receipt CLI，不改已安装 Bridge 或记忆监听器。
- 在 A03 接通后继续 A07：提供绑定任务的 status/checkpoints/search/continue/explain 入口，恢复必须指定精确快照，返回可复制 argv。

## Capabilities

### New Capabilities
- `bridge-requirement-evidence`: 版本化单向导出、有效要求编译和精确回执。

### Modified Capabilities
无。

## Impact

新增标准库适配模块和本批消费者测试；Store 增加原子上下文发布以共同提交工件、派生任务要求和快照。不新增依赖或数据库表。A06 等后续条目仍继续排队。
