# Proposal

## Why

C06的前置链E08/E01依赖F04：既有M clean只处理平面文本和有限凭据形态，嵌套参数、已处理值以及精确Rail原文的脱敏投影缺统一边界。先补采集脱敏，才能安全保存工具/用户事件并接时间线。

## What Changes

- 提供有版本、来源角色和原始/已处理类型区分的采集sanitizer，覆盖嵌套敏感字段、CLI参数、token/URL/PEM等已知凭据形态，错误不回显输入。
- 接入既有M listener/显式导入/retain持久化与缩略前路径，保留原队列、main和后台所有权，不另建观察器。
- Rail可信宿主仍显式保存精确原始bytes；新增独立脱敏派生版本与来源映射，偏移属于各自版本。

## Capabilities

### New Capabilities

- `redacted-capture`: 长期记忆采集前脱敏与精确任务证据的独立派生边界。

### Modified Capabilities

无已归档主spec。

## Impact

新增纯stdlib sanitizer与Rail可信CLI适配；项目中保存M集成补丁/部署源，备份后接既有app入口。M知识候选规则、F01事件ID迁移、F02捕获所有者和E01事件归一化继续保留各自范围，不把F04当作这些任务完成。
