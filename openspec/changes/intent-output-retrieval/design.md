# Design

## Context

B02原manifest固定call identity和两流ref；C03自动随写入建立当前head的FTS索引。现有project_result填满头尾预览预算，不能直接拼intent而超界；全task检索会把其他调用或manifest.argv误当该次输出。

## Goals / Non-Goals

只做显式intent与同一结果内继续检索；不执行命令，不推断自然语言异常结论，不替换长期main/当前硬要求。未设置intent的展示路径保持原契约。

## Decisions

1. capture(data, intent=...)与CLI --intent属于展示参数，不写入观察manifest或幂等身份。重新投影同一call换查询不增加任务版本。
2. result_search先验证manifest的schema/scope/call id及规范流ref，固定两流source。C03内部within过滤与仅对这些refs的覆盖状态；旧rev非current则pending，不能自动拿新head替换。
3. 查询响应包括标题、匹配词、围绕实际匹配原文的预览、精确span；提示词从每流前4个已索引片段采样，注明sampling，不冒称全量distinctive词。分数仍是C03词法BM25，不是语义摘要。
4. intent投影先计算保留完整执行信封/两流ref及固定intent元数据的最低预算，在保存前拒绝装不下的请求。提交后生成候选，超界依次省略词提示及整条hit，保留状态/total/省略数和继续工具。检索失败不回滚已完成的观察，而以显式状态保留原文ref。
5. MCP context.result_search与CLI result-search只读；同一read transaction内校验manifest、限定索引、构造预览。scope/session来自宿主，输入只接受result_ref/query/limit/offset/budget。

## Risks / Trade-offs

FTS不可用或旧流已不current → 明确unavailable/index_pending并可回读。采样提示并非完整词表 → 返回sampling标签，不能据此否定未出现词。极窄预算可能无hit → 明确展示省略，不能变成no_match。大输出hash/读取成本依旧由当前Store承担，不宣称已测得token/延迟收益。

## Evidence

https://github.com/mksglu/context-mode/blob/a92cf6a77ac02971fb5fafe2155e5ee229491884/src/server.ts 的intentSearch与未命中后的继续查询机制。上游固定字节阈值与执行器不直接引入。
