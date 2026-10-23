# Proposal

## Why

E01已保存原生证据，但producer只是标签，没有唯一入口的认领。恢复包和自有派生页仍可能作为新资料重新进入学习队列，旧前缀过滤还会丢失注入块之后的真实用户纠正。

## What Changes

- 确定现有JSONL为原生会话唯一capture_owner，可选Hook捕获入口仅确认委托、不重复保存/学习；显式导入有独立producer/owner标记，原MCP召回/启动注入保持。
- 为任务包、注入包、事件与生成页贯通source_event/derived_from/history_origin。
- 只对自动学习做来源投影：恢复/注入/自有生成资料保留证据与过滤原因，真正用户回应仍入候选。旧queued/ready_to_save任务进入模型或保存前执行同一规则。
- 提供本地有界owner/过滤诊断，并以真实恢复资料验证重复恢复不产生新知识效果。

## Capabilities

### New Capabilities

- `capture-ownership`: 原生与显式导入的唯一采集入口及委托回执。
- `learning-origin`: 原始证据与自动学习的来源投影、派生链和纠正保留。

### Modified Capabilities

无已归档相关主spec。

## Impact

沿用原listener/main/state.sqlite、F01身份与F04处理。默认不安装新Hook、改变原生信任或引入新observer。不是可随意切换的多捕获后端；原生capture_owner固定原JSONL入口，Hook无需第二次学习。F08/E08/C06继续原范围。
