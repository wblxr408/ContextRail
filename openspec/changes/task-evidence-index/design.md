# Design

## Context

Store._put是工件写入公共事务内入口，invalidate/purge是失效清理入口；现有search逐件加载做子串匹配。schema6已有权威任务/快照，不应在迁移时长时间加载全库或改变其版本。结构分块和中英文词法规则可复用。

## Goals / Non-Goals

本批实现C03派生FTS候选及精确跨度，保留字面入口；C02外部编辑/抓取监听、C04拼写融合、B04意图输出接线在后续实施。索引不是长期知识权威，不生成模型摘要。

## Decisions

1. schema7增加每个当前head的index_state，及派生FTS虚表。迁移只建立待回填状态，原任务/快照不动。没有FTS5的SQLite构建明确backend unavailable，核心保存/字面回读保留；不动态下载扩展。
2. 新写入在同一事务准备分块、删除该scope/name旧索引、插入name/revision/span/source身份和state。无效UTF8 excluded；超过16MiB或4096片段pending，不保存前缀冒充全量。SQLite索引提交错误让原写入回滚。
3. 复用StructuralChunker，过大结构进一步按UTF8边界/换行切成最多8192-byte片段。词法规则沿用既有英文/标识符完整词与中文词/bigram；词编码为ASCII hex token，避免FTS tokenizer拆坏路径或把查询当表达式。保留词频，名称列加权。查询最多16个去重词，默认OR候选，原词和未用词可见。
4. FTS表共享，SQL始终按scope/current head/indexed state/有效期过滤；BM25分数使用共享索引统计，只作同次查询相对排序，不能比较不同任务分数或声称语义相关。返回候选通过正常Store.get校验原文与来源，片段仍为不可信证据。
5. index_status为只读覆盖统计；重建受owner/freeze/epoch约束，按任务重新派生，不递增任务版本，保留其他任务。查询为只读，不能自动重建或迁移；旧库pending不被模型工具偷偷回填。
6. 新增context.search_indexed/context.index_status，以及search-indexed/index-status/index-rebuild CLI，既有literal search不改。整个JSON按reader/response bytes限制，省略完整条目保留next-read/source通道。

## Migration Plan

在默认Store初始化事务内扩展schema7，填metadata pending，再由明确owner调用index-rebuild回填。每次重建可重试，业务效果是同一来源集合的可查询派生视图。旧程序拒绝新schema，不能直接向下改版本；回退时保留升级前库/使用新版代码，派生FTS可重新生成，不能丢权威任务后只恢复索引。

## Risks / Trade-offs

SQLite构建缺FTS → 明确不可用，不替换为伪FTS。FTS副本与权威不一致 → 事务提交、当前head/来源join与正常原文读取；外部破坏派生索引后显式重建。大词/单字符召回有限 → >512-byte词不入词表，中文bigrams保留；短词/模糊融合属C04，字面通道仍可用。8192-byte边界可能断开跨块词 → 明确片段候选，不宣称发现全部匹配。

## Evidence

https://www.sqlite.org/fts5.html 的BM25排序、事务/索引一致性与rebuild说明；本项目使用自维护派生内容表，不把FTS rebuild命令当作权威回填。
