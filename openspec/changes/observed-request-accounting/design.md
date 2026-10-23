# Design

## Context

详见proposal。已有RequestBudget、UsageLedger/RequestUsage、CompactionEstimate及R events；M原监听器已有增量游标和normalized_events，但未保留token_count。保持Python/SQLite与原owner，本阶段集中实现再统一验收。

## Goals / Non-Goals

主机提供最终序列化payload与显式分项字节范围；只存计数和标识，不存正文。原生usage与Rail请求观测分开展示，当前宿主最终请求不可见时仍unknown。没有新API Key、付费请求、模型调用或默认observer。

## Decisions

1. R schema10给原request_usage加observation_json和按scope/session/观测ID唯一索引，工具计量复用原events。UsageLedger接元数据；新HostAccounting SDK/CLI负责可信宿主入站和幂等，不给MCP写入权限。
2. 最终payload按真实bytes记录；system/tools/history/working_set/other只允许不重叠范围，完整包的剩余字节是framing/unclassified。没有payload时总量未知；输出/工具/安全预留带自己的unit，不和实际输入相加。tokenizer只接受SDK可信回调与明确ID；CLI只接宿主已报告usage，不运行模型计数端点。
3. OpenAI usage按官方子集口径，cached不额外加入input、reasoning不额外加入output。来源：https://developers.openai.com/api/docs/guides/agents-api/observability 与 https://developers.openai.com/api/docs/guides/token-counting（2026-10-23实读）。无money/pricing换算。
4. 适配context-mode固定90af14e的Codex parseCodexUsage字段提取到ELv2 Python件，改为严格非负整数字段/缺值null，保留last与cumulative，按原事件身份认领重复。只在原listener捕获，原生报告保持会话来源，未证明与某Rail请求关联。报表用最新已报告累计，不把每条累计求和。
5. 日常MCP调用在既有adapter记录应用返回JSON bytes/读回bytes/耗时，业务调用结束后复用原R连接另开审计事务，不给模型增加可写工具。统计写失败不能丢掉业务结果，当前reader保留计量失败，跨进程未记录范围未知。请求身份由可信主机绑定；原生JSONRPC调用ID只标工具call，不冒称provider request ID。统计复用现有边界，不增加后台。
6. OutputRecorder实际保存raw与returned bytes关联同call，回读保留独立receipt；索引量只标存储/处理量，不作为节省。新context.metrics与CLI metrics展示同口径已知部分、unknown数量、请求和工具页；不同unit不合计。
7. 收益建议action=compaction/cache，trigger只允许明确边界/源变化/预算压力/回读尖峰/显式请求。成本为共同unit的显式估计，未来轮数不足/缺值/非正收益则保留原包；复用CompactionEstimate判正收益，无自动生成。只因turn_count返回无有效触发。
8. 在主机SDK中提供begin_if_efficient与compile_if_efficient接入原CompactionLifecycle/CacheLayoutPolicy；低收益保持原包且无准备记录，正收益只允许主机显式准备或套用layout。MCP建议仍只读，provider cache命中未知，原手动begin保留主机明确覆盖语义。

## Risks / Trade-offs

原生数据口径/粒度不等于provider账单→标native_reported，保留原source_ref与缺口；未知usage不填0。大响应追踪不能成为额外负担→仅保存有界计数/标识，不复制正文。迁移可能让旧RO worker版本不符→备份原R，按新源码重连；不改旧Hook信任。

## Migration Plan

整阶段消费者先基线，再集中接线，统一验收；具体失败才定点修复。R备份后升级schema10，原M备份并最小部署原生usage适配，由原control重启。实际捕获/读取指定原生usage和MCP返回计量，最终请求未观测仍unknown；更新D07/H03/D08及验收，整体goal继续active。
