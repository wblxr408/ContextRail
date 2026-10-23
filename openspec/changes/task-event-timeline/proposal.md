# Proposal

## Why

E01保留了可恢复事件，但按采集序号浏览，无法可靠回答错误前试过什么、用户纠正何时发生。E08需要带原证据引用、来源缺口可见的时间线，为后续C06统一查询提供事件入口。

## What Changes

- 原事件库及Rail任务内增加可重建时间索引；按实际occurred_at排序，无时间单独置后。
- 会话、文件、角色、来源、错误类别和时间过滤，以及围绕选定事件的前后浏览。
- 提供任务绑定context.timeline/CLI与原M精确会话只读入口，分页/预算/未索引及不可用来源明确。
- 适配ai-memory的范围先过滤再分页方式，移植context-mode错误分类纯函数并保留ELv2。

## Capabilities

### New Capabilities
- `event-timeline`: 可追溯、按真实时间排序的任务及原生会话经历浏览。

### Modified Capabilities

无。

## Impact

既有Python/SQLite、Store artifact写入、原M事件索引、只读工具与CLI。派生表复用原库，不增加observer/模型/外部运行时。旧库只种下pending元数据，显式有界补建，原证据及任务版本不改。
