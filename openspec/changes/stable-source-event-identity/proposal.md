# Proposal

## Why

F01是事件归一化及任务经历查询的前置。现有listener以session/marker/final拼接后的正文参与身份，异内容重送会生成新job，缺原生ID时不同事件的相同正文又会被合并。

## What Changes

- 明确命名空间、workspace、原生session及turn/message ID，正文只作为内容；缺原生身份时用来源文件代际及记录位置的弱身份并声明边界。
- 沿用ai-memory的来源键认领与事件原子提交机制，适配现有jobs而不引入其运行时；增加旧job别名和有内容差异的冲突报告。
- 显式文档/Git导入也使用相同认领机制，保留旧分片ID映射，拒绝在同一源修订下静默增加/减少分片。
- 为本地可信入口提供有界事件身份/冲突状态，供后续Rail事件桥按稳定ID定位。

## Capabilities

### New Capabilities

- `source-event-identity`: 原生及显式导入事件的稳定来源身份、原子去重、旧队列映射和冲突报告。

### Modified Capabilities

无已归档相关主spec。

## Impact

复用Python/SQLite、既有listener/history_import/main与F04策略。增量来源表属于原M state.sqlite，不是新知识权威。F02采集owner、F08完整日志轮换与E01事件归一化仍独立；不修改已保存事实的事件ID。
