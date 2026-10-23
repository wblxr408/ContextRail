# Proposal

## Why

当前UsageLedger已有请求粒度账本，但日常宿主未把最终请求、工具返回与后续回读串起来；小包字节数容易被误读为真实token或整体收益。D07/H03/D08合为一个阶段，补实际观测与估计之间的边界。

## What Changes

- 给现有请求账本补宿主最终payload/分项字节、真实tokenizer或usage来源、预留和未知字段，不保存原始提示正文。
- 在现有MCP读路径持久记录有界工具计量，关联宿主请求或明确未知，重连不丢统计；只读工具展示逐请求、回读和覆盖缺口。
- 适配context-mode的Codex usage提取到原事件采集owner，保留null/累计口径，避免把reasoning重复加到output。
- 用原CompactionEstimate接事件触发的摘要/缓存净收益建议，计入生成、校验、回读、缓存损失和未来轮数；不自动调用模型或付费服务。

## Capabilities

### New Capabilities

- `request-accounting`: 宿主最终请求与来源明确的计量。
- `tool-observability`: 持久工具返回/回读/索引统计与原生usage覆盖。
- `efficiency-advice`: 基于显式单位和成本的事件触发收益建议。

### Modified Capabilities

无主spec；复用现有请求usage账本、原R events与原M normalized_events。

## Impact

沿用Python/SQLite/main监听器，无新默认observer/模型服务。R schema10为request_usage补观测元数据列及幂等索引；工具计量复用原events，原生usage复用原事件库。影响policy/store、MCP/CLI、controller与compaction适配、新ELv2移植件和原M最小部署。
