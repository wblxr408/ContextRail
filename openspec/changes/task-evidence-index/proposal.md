# Proposal

## Why

已保存的大输出目前靠逐件子串扫描，无法直接按有来源的候选片段排序。C03 为任务证据增加可重建的 FTS5/BM25 索引，使检索结果携带精确 revision/span，并明确未索引范围。

## What Changes

- 新增独立的索引搜索、状态和受owner租约约束的按任务重建入口，保留现有字面搜索契约。
- 新证据、版本替换与索引状态一致提交；失效/过期/清理不再返回不可读候选。
- 旧库升级只建立待索引元数据，显式重建派生索引，不改任务权威或快照。
- MCP只读查询与CLI接通；预算、来源定位、部分覆盖及FTS不可用均可见。

## Capabilities

### New Capabilities
- `task-evidence-index`: 有版本/字节跨度身份的任务证据全文索引、覆盖状态与可重建读取。

### Modified Capabilities
无。

## Impact

Store schema升级至7，新增派生索引模块与ContextTools/CLI入口。沿用Python/SQLite，不新增记忆服务、模型或第三方依赖，长期知识仍在main。
