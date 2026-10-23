# Proposal

## Why

现有FTS按结构块返回首160字符，词位于段落中部时预览看不到命中；result_search自行截字符但没有对应窗口ref或全局行号。C05把当前词法候选转为可精确回读的局部证据，保留重复标题下的不同原文位置。

## What Changes

- 命中返回原结构ref、标题、局部窗口及全局行号，每个窗口/词位都有真实UTF8字节区间。
- 合并同一结构段内重叠/相邻命中窗口，分离远距离命中，超限明确省略；同名标题的不同区间保留。
- 任务索引和原两流结果共用窗口算法，CLI/MCP支持有界上下文与窗口数量，完整JSON继续按整项裁减。

## Capabilities

### New Capabilities

- `source-hit-windows`: 词法命中上下文、全局行号与字节精确锚点。

### Modified Capabilities

无已归档主spec；在现有任务索引与意图检索之上新增窗口行为。

## Impact

复用现有Python/SQLite/FTS及Scope读取，新增纯窗口模块并接indexing/result_retrieval/host/CLI。无schema升级、索引算法或依赖变更。窗口只用于回读，代码依赖继续由宿主SelectionGroup表达。
