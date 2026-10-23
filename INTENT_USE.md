# 按意图查看工具输出

B04 在宿主已经执行命令后，为 capture-result 增加可选 --intent。原始两流、执行信封和索引先保存，再返回相关原文候选。它不执行命令，也不把查询命中当成执行成功。

## 捕获时给出意图

```powershell
.venv/Scripts/python.exe -m contextrail capture-result --workspace D:/my-project --task my-task --session owner --epoch 0 --result observed.json --intent 'ERR-8372' --response-bytes 4096
```

Python 使用 `OutputRecorder(...).capture(observed, intent="ERR-8372", budget=4096)`。intent是独立展示参数，不放进执行观察JSON、不进入原manifest或幂等身份；不传intent时沿用原头尾预览。

intent.hits提供stream、标题、实际匹配词、围绕匹配位置的原文预览和精确ref。标题/预览可以缩略并标记，原ref仍能完整回读。响应顶层status/exit_code仍是实际执行状态，例如命令failed但intent.status=matched，表示找到了诊断而不是命令变成功。

## 首次没找到后继续

调用只读 `context.result_search`：

```json
{"result_ref":{"name":"tool-result/call-1/result","revision":1},"query":"another diagnostic","limit":5,"budget":8192}
```

或CLI：

```powershell
.venv/Scripts/python.exe -m contextrail result-search --workspace D:/my-project --task my-task --session owner --result-ref '{"name":"tool-result/call-1/result","revision":1}' --query 'another diagnostic' --response-bytes 8192
```

该查询只搜索原manifest指向的stdout/stderr，排除该manifest.argv和同task的其他调用。scope/session来自宿主绑定；输入没有任意源路径、scope或执行参数。每条hit.ref可直接交给context.get。

结果还给出searchable_terms供换词。词提示仅从每流前4个有效索引片段采样，hints_sampling明确说明，不是全量词表；失效/过期/历史未索引内容不提供可查询词提示。

## 状态与预算

| 信号 | 应如何继续 |
|---|---|
| matched | 查看候选或用ref读原文，不能据此改写执行状态。 |
| no_match | 当前词法查询未命中已索引流，换词或读原文；不表示没有错误/异常。 |
| index_pending | 原始流尚未索引、已成历史revision或不可用，查看index.reasons，保留原ref。 |
| unavailable / unsupported_query / budget_limited | intent投影失败/不支持/展示预算不足，原执行结果及引用仍返回，可换查询或直接回读。 |

完整捕获投影先核对必需信封、两流ref和intent元数据的最低预算，装不下则在保存前拒绝。保存后可选检索失败不会抹掉已经持久化的观察；响应保留原执行状态、原ref和明确降级信号。超预算按词提示、整条hit依次省略，并报告total/returned/omitted，不截断JSON。

intent最多1024字符；继续查询每页最多20项，支持offset分页。当前是C03的词法候选，不做语义总结或模糊纠错。原manifest若指向旧流revision，而head已前进，返回historical_revision_not_indexed，绝不搜索新head冒充原输出。原manifest格式不完整或身份不符会拒绝。

## 本项目实际使用

已在当前ongoing task实际捕获宿主执行的 `git status --short`：先查询 contextrail/result_retrieval.py，再对同一结果换词查询 contextrail/indexing.py，并按ref读回原始工作区输出。4096byte额度下首包1758bytes；回执在 `.context-rail/b04-worktree-receipt.json`。这是本机真实观察，未把子进程夹具当成实际工作区。

本次保存推进了任务version，因此运行时视图已从固定base刷新；`.context-rail/active-native-client.json`保留snapshot/base绑定，同时新增current_snapshot=a13cf068a6264c6cbac8e7f21c361fca。后续读取当前包应使用明确的当前视图；原base仍用于来源/刷新绑定，不能把旧版本强行当成当前。

当前用例与边界见 openspec/changes/intent-output-retrieval/verification.md。
