# Spec Delta

## Purpose

让调用方为已执行并保存的工具输出指定查询意图，优先看到相关原文段落、可查询标题和精确来源。首次未命中后仍能换词继续搜索或直接读原文，检索状态不能覆盖执行失败，也不能被解释为没有异常。

## ADDED Requirements

### Requirement: Result bound intent projection
系统 SHALL 在保存及索引后按显式intent返回相关片段、标题与引用；查询 MUST 只针对该次结果manifest指向的stdout/stderr，不混入其他调用或元数据。

#### Scenario: Failed command with useful diagnostic
- **WHEN** 失败命令产生很长诊断，相关错误在输出中部
- **THEN** 小包保留执行failed并给出该错误原文候选，不因检索命中改成成功。

### Requirement: Continue after no match
系统 SHALL 区分matched/no_match/index_pending/unavailable，并提供换词检索及原文读取依据。未命中 MUST 不表示没有异常；派生查询不更改已执行观察身份或任务版本。

#### Scenario: Another query on the same call
- **WHEN** 首次intent未命中，调用方换词再次查询或重送同一观察
- **THEN** 原manifest及stdout/stderr不变，新的查询可定位原数据且不重复保存业务结果。

### Requirement: Complete bounded projection and provenance
系统 SHALL 对完整intent投影核算UTF8预算，保留执行状态和必需引用，按整项省略候选/词提示；连最低投影装不下 MUST 在保存前拒绝。历史或未索引source必须明确，不能把旧引用换成最新流。

#### Scenario: Changed stream head
- **WHEN** 结果manifest仍指向旧revision而该流head已经前进
- **THEN** 标记历史未索引并保留原ref读取，不搜索新revision冒充原输出。

### Requirement: Bound readonly continuation tools
系统 SHALL 将继续检索接入现有绑定任务的只读MCP与CLI，不接受scope/执行覆盖或任意源路径；malformed结果manifest MUST 拒绝。

#### Scenario: Real MCP search and cold read
- **WHEN** 客户端对已保存结果换词搜索
- **THEN** 返回同一调用的相关span，可按ref读回原始中文/CRLF；越权参数拒绝。
