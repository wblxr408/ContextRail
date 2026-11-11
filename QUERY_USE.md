# 统一查询、重复处理与状态入口

本阶段一次交付C06/C08/H01。沿用既有MCP/CLI、R任务库、原生事件库与main资源服务；没有新增observer或模型服务。原文仍分别由context.get、原M stream-get和Basic Memory read_note拥有。

## 可信来源绑定

宿主配置文件示例：

```json
{
  "schema": 1,
  "workspace": "D:/ContextRail",
  "task": "self-use-01a161c7",
  "memory_home": "C:/Users/wblxr/.codex/services/engineering-memory",
  "native_sessions": [{"client": "codex", "session": "01a161c7-38d2-7333-a025-9d0be8d6a62a"}],
  "memory_mode": "text",
  "timeout_seconds": 12,
  "snapshot": "75891fb8b28d446999ef355f73ba1ef0"
}
```

MCP启动命令加`--query-binding <此文件绝对路径>`；client-prepare也接受该参数。配置限定workspace/task及最多4个精确原生会话，模型不能在query参数中改路径或会话。两客户端可共用一份显式绑定；不会自动选择“最近会话”。缺少绑定时，任务搜索仍工作，其余来源明确not_configured。

本机阶段应用后使用`.context-rail/clients/self-use-01a161c7/v1/query-sources.json`。原memory_home和recall继续归main服务；text模式不启动新的语义模型。显式hybrid仍只使用原有共享模型/预算。

## 批量查询

MCP `context.query`请求：

```json
{"queries":["来源事件","恢复材料"],"view":"relevance","limits":{"task":4,"events":4,"memory":3},"budget":24000}
```

CLI把同一JSON写入文件：

```powershell
.venv/Scripts/python.exe -m contextrail query --workspace D:/ContextRail --task self-use-01a161c7 --session host-01a161c7 --query-binding D:/ContextRail/.context-rail/clients/self-use-01a161c7/v1/query-sources.json --input query.json
```

省略input则从stdin读取。JSON显式budget优先，省略时CLI沿用项目response_bytes；服务器read_bytes仍是上界。

一次1–8个查询，两种视图都查询三源。task沿用FTS5/BM25；events在精确来源范围内匹配处理后原文词项；memory沿用原recall。relevance按来源内排名合并，不比较不同引擎的原分数。timeline仅将匹配候选按已知来源时间排序，未知时间置后；不是整个历史的无遗漏时间线。

每项包含origin、task/source_session、version、time、ref和evidence_role。task只表示已归入当前任务的已保存revision，不能据此断言外部文件已同步；events和memory明确为历史，不提升成当前task事实。日期只有天、缺时区、标题推断日期都不当作精确发生时间；memory recall不提供精确历史revision时标unavailable，read_note读取其当前原件。

since包含/until不包含，均须带时区。task/memory的时间范围作用于当前候选页，覆盖元数据明确global_time_search=false，可继续下一来源页。events的时间范围在原库查询内过滤。各源分别提供total、fetched、shown、覆盖缺口及next_offset；memory offset沿用页大小的整数倍，预算省略其部分候选时返回本页重试，避免跳过未展示原文。

## 标识符、路径和中文短词

`search-indexed`、`context.search_indexed` 和统一查询中的任务来源使用同一份索引。中英文混合的路径支持 Windows 和 POSIX 分隔符，命中保留原文中的写法与字节范围。中文单字、双字可直接查找，多字词按连续字组匹配。普通词项在同一片段中相邻时优先显示。

`useEff` 或 `useEfect` 没有精确结果时，可能在 `suggestions` 看到 `useEffect` 和可回读的 Ref。统一查询把这类候选放在 `sources.task.coverage.suggestions`。路径、版本号和错误码按输入匹配，即使附近正文也包含查询里的普通词。该实现参考了 [context-mode 的检索分层](https://github.com/mksglu/context-mode/blob/a92cf6a77ac02971fb5fafe2155e5ee229491884/src/store.ts)和 [ai-memory 的中文索引](https://github.com/akitaonrails/ai-memory/blob/1c86aebc14addccc7ac875913d565c681bfa4a50/crates/ai-memory-store/src/fts_query.rs)。

旧索引的原有词项仍可查询。出现 `lexical_upgrade_pending` 时，用当前任务所有者重建该任务的索引，让新词项也覆盖旧材料。

```powershell
contextrail index-rebuild --workspace D:/ContextRail --task my-task --session owner --epoch 0
contextrail search-indexed --workspace D:/ContextRail --task my-task --session owner --query '圆锥曲线'
```

## 有来源的网页缓存

先用现有浏览器或连接器抓取页面，通过 `capture-result` 保存实际响应正文。宿主把正文的完整 Ref、抓取时间和 HTTP 信息写入 JSON，再提交一次观察。缓存键由任务作用域、可信宿主指定的 `source` 和请求 URL 组成，跳转后的最终 URL 单独保留。该身份组合参考 [context-mode 的抓取缓存](https://github.com/mksglu/context-mode/blob/a92cf6a77ac02971fb5fafe2155e5ee229491884/src/fetch-cache.ts)。

```json
{
  "source": "browser-docs",
  "url": "https://example.com/docs",
  "status": 200,
  "fetched_at": 1790000000,
  "ttl_seconds": 900,
  "http_version": "2",
  "etag": "\"v1\"",
  "expected_revision": 0,
  "fetch_id": "docs-fetch-1",
  "ref": {"name": "tool-result/docs-fetch-1/stdout", "revision": 1}
}
```

```powershell
contextrail web-cache-store --workspace D:/ContextRail --task my-task --session owner --epoch 0 --input observed-response.json
contextrail web-cache-get --workspace D:/ContextRail --task my-task --session owner --source browser-docs --url https://example.com/docs
contextrail web-cache-get --workspace D:/ContextRail --task my-task --session owner --source browser-docs --url https://example.com/docs --refresh
```

把示例时间和 Ref 换成实际抓取结果。读取时只有 `fresh` 提供 `read_ref`。到期或显式要求刷新时，根据 `conditional` 让原连接器发出请求。收到 HTTP 304 后，用返回的版本填 `expected_revision`，提交新的 `fetch_id`、抓取时间和原样的 `validator`，省略 `ref`。收到新正文时则提交新的完整 Ref。缓存读取也可用 MCP `context.web_cache`，它返回本地观察和刷新状态。历史 Ref 保留原始证据的回读路径。

## 重复与继续

同批完全相同查询只执行一次。常驻MCP/SDK读会话另有有界内存缓存：R绑定任务/索引/过期状态，events绑定所选会话capture序号，memory绑定原服务返回的目录/检索配置版本。版本无法确认或来源失败时不以旧成功缓存冒充新鲜结果。缓存不写第二个memory store；CLI独立进程之间不共享缓存。

重复已展示段落返回空preview、already_shown及repeat_of，仍保留精确ref。响应预算省略、先前未展示的候选不会被错误标成“已看过”。用户可改查询/offset，或用`"refresh":true`重新获取完整预览；context.get等必需回读始终可用，没有次数封禁。

remaining_bytes使用完整JSON预算的保守余量，response_bytes_upper_bound明确是上界，实际返回仍严格按完整UTF-8 JSON限额。先省预览再省候选，省略数和重试位置可见。来源内各页最多20项、每源2–16KiB，整批2–64KiB。SQL/RPC有合作式期限；一个源失败不隐藏另外的结果。

## 一个状态入口

```powershell
.venv/Scripts/python.exe -m contextrail status --workspace D:/ContextRail --task self-use-01a161c7 --session host-01a161c7 --query-binding D:/ContextRail/.context-rail/clients/self-use-01a161c7/v1/query-sources.json
```

MCP对应`context.status`。它显示task/version/owner、存储中的当前需求及可核对的Bridge源、所选会话raw捕获数量、workspace知识队列、最近来源冲突/本读会话查询失败，以及最近native_context.prepared回执。捕获不等于知识保存，准备包不等于模型已消费；只读能力不等于控制出站请求。缺业务证据处保持unknown，不用旧心跳当在线证明。状态读取不发模型/recall请求。

原M recall仅在此适配器显式include_revision时返回目录token，普通调用不会携带整个目录版本列表。目录token用于缓存失效，不冒充笔记历史revision。

原记忆服务在空闲回收后重新加载，可能超过本地查询期限（本机绑定20秒、整批合作式期限24秒）。此时memory槽明确返回unavailable，task/events仍可用；原服务就绪后可再次查询。text只表示不用语义推理，原服务仍可能有冷启动成本；本阶段没有调整其资源回收策略。

本阶段统一消费者在tests/test_query_stage.py，应用及实际验收记录在openspec/changes/federated-context-query/verification.md。已运行的旧批次不在本阶段验收范围内。

## 跨会话经验审阅

用可信来源绑定选择同一工作区的 2–4 个原生会话。需要限定日期时，传入带时区的 `--since`（含）和 `--until`（不含）。从项目当前任务执行：

```powershell
contextrail experience-review --workspace D:/ContextRail --task my-task --session owner --query-binding D:/path/experience-sources.json --response-bytes 16384
```

审阅列出重复完成的步骤、重复出现的相邻两步操作和同一操作的重复失败。每个候选至少有两个独立原生会话的证据。Shell 按命令和执行环境匹配，桌面代码工具按捕获的脚本匹配。失败分类来自原时间线的启发式规则。候选包含调用和结果的精确 `memory.stream_get` 引用，可以通过[原生事件回读](NATIVE_EVENTS_USE.md)核对。审阅只读取已捕获的事件，输出供人工判断。

每个会话在选定时间范围内检查最近捕获的最多 400 条工具事件。`coverage` 给出较早事件是否省略，以及有多少次调用缺少可用的结果。桌面代码工具的包装层显示完成时，仍需捕获明确的子进程结果才能计入。未捕获、被策略隐去、超限、尚在运行或无法确认结果的调用，均留在覆盖信息中。用 `next_offset` 翻候选页，或缩小时间范围回看较早的事件。

要否决候选，用当前任务所有者和租约 epoch，带上审阅输出中的 `id`、`evidence_version` 以及同一绑定和时间范围。

```powershell
contextrail experience-reject --workspace D:/ContextRail --task my-task --session owner --epoch 0 --query-binding D:/path/experience-sources.json --candidate <id> --evidence-version <evidence_version> --reason "这个步骤只适用于一次性迁移"
```

原记忆库保留该次决定的理由、时间和证据版本。原样重送返回原决定。相同证据及其子集继续隐藏，审阅返回 `suppressed_count`。新证据会重提候选，并给出 `new_evidence_event_ids` 和上次否决理由。用户也可以在审阅命令中传入 `--reconsider <id>`，把仍有证据的指定候选放到结果首位，同时保留原决定。
