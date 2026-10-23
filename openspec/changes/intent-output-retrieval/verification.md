# B04 验收

2026-10-22，Windows/Python3.11。本轮只实现显式意图投影及结果内继续检索。

## RED → GREEN

先由真实Python子进程生成6000行含中文/emoji/CRLF的噪声，中间写ERROR ERR-8372诊断、独立stderr并退出7，再通过capture-result --intent请求中部错误。首次真实RED为缺--intent、退出2。实现后8项初始行为通过，保持顶层failed/exit=7及意图matched，原始来源可回读。

后续三个明确失败取得RED：SQLite authorizer拒绝bm25后泄漏OperationalError而隐藏已保存引用；身份/两流均正确但缺status的manifest泄漏KeyError；过期来源仍从词提示路径泄漏token。分别修复保存后的可选查询降级、manifest状态校验、提示词与候选一致的current/indexer/expiry过滤。

最终 `.venv/Scripts/python.exe -m unittest tests.test_intent_output_retrieval`：**11项通过，3.917s，无跳过**。

| 当前需求 | 实际观察 |
|---|---|
| 中部诊断/失败状态 | 实际失败子进程→CLI捕获，小包≤4096bytes，中部错误在title/snippet/ref中，原CRLF与中文可回读；退出7不软化为成功。 |
| 未命中后换词 | 首次absent词no_match，同一result_ref通过result-search CLI换switchneedle后matched，并保留提示词和原文引用。 |
| 结果边界 | argv里的metadataonlyneedle不作为流匹配；同task另一call中的crosscallneedle不混入；stderr匹配标记stderr。 |
| 幂等与展示意图 | 同一观察换intent重送，manifest原文与task version不变，新的stderr查询能返回另一个片段。 |
| 最低/整体预算 | 1800byte不足以容纳长intent必需投影时零保存；2200byte小包整项省略仍保留failed、total与omitted。 |
| 历史source | stdout head推进后，原结果查询不匹配新head，返回index_pending，ref保持旧revision可读。 |
| 检索降级/格式 | 无词法项的intent保存成功后unsupported_query；查询SQL被真实拒绝后仍返回unavailable与已保存refs；malformed/missing-status manifest拒绝。 |
| 过期词提示 | 用实际expires_at流revision与对应manifest，时钟过期后hits与提示均不含该流内容，unavailable计数可见。 |
| 真实MCP | 官方SDK启动真实stdio server，从未命中结果继续查switchneedle，再按ref读到ERR-8372中文CRLF；task override拒绝。 |

## 实际ongoing task

本机宿主实际执行 `git status --short`，观察call_id=worktree-dfc1667130c3。OutputRecorder按contextrail/result_retrieval.py查询，execution=succeeded/exit0、intent=matched、首包1758bytes；同一结果换contextrail/indexing.py也matched，ref实际读回原工作区输出。

新观察正常推进task version至14。Bridge从固定base a558284943e241eab297739aca0864f2刷新到a13cf068a6264c6cbac8e7f21c361fca，当前目标仍为根据todo做出持续实现。active-native-client.json新增current_snapshot，原base与owner绑定保留。完整回执 `.context-rail/b04-worktree-receipt.json`。

## 构建与限制

最新源码wheel离线非隔离构建成功；bdist_conda缺模块警告非致命。OpenSpec strict与git diff --check通过。没有执行旧批次/全项目回归或smoke/hash/浅启动探针，不声称旧套件回归通过。代码未提交/推送。

命中是词法候选，词提示有采样范围；没有模型调用、异常自动判断或新服务。C03通用查询保留原入口，B04仅增加内部明确两流过滤，不能给模型重绑scope。
