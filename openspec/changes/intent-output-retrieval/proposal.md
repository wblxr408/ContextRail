# Proposal

## Why

大输出已完整保存并建立索引，但默认头尾预览未必包含用户想看的内容。B04 增加显式intent投影与按该次结果继续查询，让首次未命中后仍可换词或精确回读，不把空摘要当成无异常。

## What Changes

- capture-result / OutputRecorder 接受独立intent查询，保存及索引完成后优先返回相关片段、标题与精确引用。
- 新增绑定单次结果stdout/stderr的context.result_search与CLI；不能搜索到同任务其他调用的输出冒充该次结果。
- 完整响应预算、未命中/未索引信号和原文恢复入口明确；执行状态与检索状态分开。
- intent只影响展示，不进入执行身份，换词不制造第二次业务捕获或改原manifest。

## Capabilities

### New Capabilities
- `result-intent-retrieval`: 已保存工具结果的意图投影、结果内继续检索及原文恢复。

### Modified Capabilities
无。

## Impact

复用C03 FTS索引、B02完整保存及现有只读MCP；不执行新命令、不调用模型、不注册新服务。
