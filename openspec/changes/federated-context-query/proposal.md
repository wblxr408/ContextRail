# Proposal

## Why

C03任务证据、E08原生事件与main长期记忆有不同入口，用户需重复搜索并手工核对范围和来源。C06把它们接入同一批量查询，来源失败与历史角色明确，便于自用排查和恢复工作。

## What Changes

- 一次1–8个查询，relevance/timeline两种视图均查询任务证据、显式会话事件与main记忆。
- 每源独立条数/字节/耗时边界，部分失败可见；每项保留精确ref、范围、版本及真实时间状态。
- 复用原M RPC recall及原事件库只读入口，宿主配置固定路径/会话，模型不能扩大来源范围。
- 适配context-mode unified.ts合并结构，使用来源内排名进行合并，补齐明确缺口与未知时间。
- 同阶段完成C08重复查询合并/按来源版本失效和H01统一状态，完成后做一次整阶段验收，遵循用户最新阶段验证要求。

## Capabilities

### New Capabilities
- `federated-query`: 带来源边界、回读引用和部分失败的多源批量检索。
- `query-reuse`: 合并重复检索并显示已查范围，保留强制刷新与原文回读。
- `local-status`: 当前任务、需求、捕获与学习队列及真实能力的统一状态。

### Modified Capabilities

无。

## Impact

新增查询适配与只读context.query/CLI，项目既有MCP通过可信query binding启用原M来源。M recall追加时间精度/来源元数据。既有Python/SQLite/main资源服务，不新增memory store、observer、默认模型或新MCP服务器。
