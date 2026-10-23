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
