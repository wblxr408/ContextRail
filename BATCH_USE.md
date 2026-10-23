# 独立证据的有界批量读取

B06 提供 `context.batch` 和 `batch-read`。一批最多32项，只接受独立的 `context.get` / `context.extract`。每项必须有唯一 id，结果按输入顺序返回；某一项缺失、参数错误或提取失败，不会吞掉其他项。

## MCP

```json
{"calls":[{"id":"read-log","tool":"context.get","arguments":{"name":"tool-result/call-1/stdout","revision":1,"start":0,"end":1200}},{"id":"errors","tool":"context.extract","arguments":{"source":{"name":"tool-result/call-1/stdout","revision":1},"kind":"log"}}],"concurrency":2,"per_call_bytes":2048,"budget":8192}
```

读取参数仍由原 ContextTools 核对，scope/session 来自宿主绑定。批处理不接受依赖字段、写动作、授权动作、命令或嵌套 context.batch；这些工作继续由宿主顺序执行。

每个 results 条目有 id、tool、request、status、delivery，以及 result 或稳定 error.code。`status=succeeded` 表示调用和提取完成；提取器 parse_failed/incomplete 会映射为 failed，同时尽可能保留含原source的提取结果。no_match 保持正常结果，不能与解析失败混用。

## 预算与继续读取

per_call_bytes 默认2048，不超过绑定 read_bytes；每个完整单项 JSON 都受限。原文过大时应给较窄字节范围，或显式提高单项预算。budget 约束完整批量 JSON，MCP 默认 min(8192, read_bytes)，CLI 使用 response_bytes。必要调用身份、状态、原请求和错误码在调度前预留，装不下则 budget_exceeded，未执行读取。

整体结果过大时先省略错误的可选 message，再整体省略最大的 result，并标记 `delivery=omitted`。调用成功/失败状态、错误码和原 request 仍保留，顶层 omitted 给出省略项数。省略的是交付内容，原始证据不被删改。可以把对应 tool/request 作为单项调用继续读取或提高预算；没有把 JSON 从中间截断的输出。

## CLI

把上例 calls 数组存为本机 calls.json，使用同一任务和会话参数：

```powershell
.venv/Scripts/python.exe -m contextrail batch-read --workspace D:/my-project --task my-task --session owner --calls D:/my-project/calls.json --concurrency 2 --per-call-bytes 2048 --response-bytes 8192
```

--calls - 从 stdin 读取，输入最多128KiB，单项 arguments 最多16KiB。配置 read_bytes 低于默认2048时，显式降低 --per-call-bytes。

## 并发与一致性

concurrency 在1至8之间，实际不超过调用数。每个并发worker使用现存任务库的独立 mode=ro/query_only 连接，不初始化/升级数据库；过期判断复用宿主 clock。SQLite连接不跨线程复用，处理器不建立新数据库或后台服务。

各项独立 read transaction；输出 consistency=independent_per_call_read_snapshot，不保证整批是一个原子时间点。宿主已有活动 transaction 时明确拒绝。内存库仅支持显式 concurrency=1，在原连接顺序读取，不复制成另一份库。标准库线程不承诺CPU提取加速。

当前行为与真实MCP证据见 openspec/changes/bounded-read-batch/verification.md。
