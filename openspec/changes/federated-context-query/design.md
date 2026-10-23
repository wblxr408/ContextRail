# Design

## Context

详见proposal。现有R FTS5/BM25、M原生事件/来源表、main资源RPC及E08时间元数据可复用。用户最新要求按大阶段完成再统一验收；本阶段整体覆盖C06/C08/H01，不逐TODO或逐小改动运行检查。

## Goals / Non-Goals

复用原库、原main服务和既有MCP，提供统一查询/重用/状态。仍用Python/SQLite；无新模型、后台、observer或memory store。H02依赖D05，留后续阶段。

## Decisions

1. 宿主query binding限定workspace/task、memory_home与最多4个精确client/session，CLI/MCP启动参数选择配置；模型只提供查询、视图、各源limit/offset、字节预算与refresh，不能选择任意路径/会话。原生来源未绑定会话时保持not_configured。
2. 三个来源组各用自己的连接并行处理批次，线程最多3；组内按输入顺序。原M RPC使用现有resource_client.rpc的期限/队列，不启动fallback。全批有期限，单源失败只影响自己的来源槽。R读连接复用open_readonly；事件库只读且SQL进度预算明确。
3. task用原BM25命中与ref；events在精确会话范围内对处理后正文作有截止时间的词项查询，保持原事件ref和时间；memory用原recall。相关性仅合并来源内排名，原分数不跨源比较。timeline只对匹配候选按已知真实时间排序，未知置后，报告各源候选上限，不伪称全历史时间线。
4. 移植context-mode unified.ts的来源合并结构为ELv2 Python模块，改为两模式三源、错误显式、未知时间不伪造。事件库参数查询借鉴其searchEvents，但移除空project跨范围规则。原文引用沿用context.get、M stream-get及Basic Memory read_note。
5. 来源版本：R任务/索引状态和过期状态；events精确会话capture序号；M新增内部query_revision并在recall返回catalog revision/笔记时间精度。缓存只在源版本可确认时使用，有界LRU只留本会话，不持久化第二份记忆。完全相同批内查询合并；跨请求重复预览省略并保留ref/repeat_of，refresh和新查询不封禁。
6. M原note解析追加time_provenance/source_session字段，日期只有天/无时区/title推断不装作准确occurred_at。M note revision无法精确读旧版本时明确未知，catalog版本仅用于缓存，不冒充笔记revision。
7. status复用task/Bridge、来源统计、workspace知识队列和已准备注入回执；不轮询health、不调用模型。能力保持A05可信证据语义，捕获/学习/准备注入/控制出站分别展示，配置或旧心跳不当作在线证明。

## Risks / Trade-offs

不同来源无单一跨库快照→各源给自己的revision/as-of和范围；读取过程中变化的来源不缓存。源版本取不到→重新查询或明确缺口，不能以旧缓存当新鲜。事件词项查询达到期限→保留其他来源并显示deadline，而不无限扫库。实时日期/时间含义不同→保留basis和精度。

实际部署观察到原记忆资源服务冷启动超过本地20秒请求期限：返回memory unavailable及task/events成功，服务就绪后后续实际调用成功。保留有界响应与明确缺口，不把一次温态通过描述为消除了冷启动成本；本阶段不改原资源服务回收策略。

## Migration Plan

先冻结整阶段消费者契约并统一观察一次基线，集中实现三个能力，再一次运行整阶段验收；具体失败才定位修复。通过后备份部署原M选定模块，写任务绑定并保留客户端原配置；实际三源查询/重复/变更与状态一起验收。更新三个TODO及阶段结果，整体goal仍active。
