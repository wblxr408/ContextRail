# D07 / H03 / D08 阶段验收

2026-10-23，按用户要求集中实现最终请求计量、持久工具/回读统计与事件触发收益接线；整体goal继续active。

## 当前消费者

tests/test_request_accounting_stage.py先冻结消费者：初始7项均在缺公开context.metrics处预期RED。集中实现后一次整阶段12项全部GREEN（10.301秒）：同packet不同最终payload、未知usage/真实回调接线、OpenAI子项不重复计、范围/错误输入拒绝、回读重开库与请求关联、失败无伪造成功、短任务/缺成本/盈亏平衡、真实MCP重连、原M usage重放/累计/null、预算unit校验、真实capture与回读、schema9数据升级、真实CLI record-request/metrics。

随后只处理新发现的具体问题及原有生命周期接线缺口：

- 并发重送原来触发裸SQLite唯一约束错误；6个真实并发writer观察RED，补Host observation冲突映射后同6次提交只保留1行、全部返回同一ID，定点GREEN（0.301秒）。
- 补CompactionLifecycle.begin_if_efficient、CacheLayoutPolicy.compile_if_efficient实际应用建议的SDK入口：2项先观察缺方法RED，接入原生命周期/layout后定点GREEN（0.283秒）。低收益无准备行、保留原包；正收益只显式准备或套layout，不调用模型/宣称provider cache命中。

共15个独立当前消费者有对应GREEN；未声称修复后重新执行完整15项。未运行旧批次/全项目回归、上游测试、smoke/health/hash/import/startup探针。

## 关键口径

原request_usage新增observation_json及scope/session/观测ID唯一索引，R schema10；旧scope/owner/snapshot/usage保存。UsageLedger复用，工具计量放原events，不是新默认observer或memory store。M usage仍由原JSONL cursor/事件owner捕获，不加第二游标。

最终请求只接可信主机实际serialized bytes/分项不重叠范围；字段缺失为null，packet大小不充最终请求量。只存计量元数据，不留最终payload正文，因此等长异内容未验证；幂等验证计量/归属一致性，不宣称业务执行exactly-once。RequestBudget带unit并与Compiler明确计量单位核对。

MCP的bytes是应用结果JSON，context.get原文bytes另计，失败保持缺值；索引/保存量不当输入削减。旧聚合和请求账本不盲合计，不同unit分别呈现；对照缺失时provider_input_savings/cost_savings为null。统计本身/efficiency不递归计量，存储失败不破坏原结果，遗漏范围未知。

按已实读的[OpenAI usage说明](https://developers.openai.com/api/docs/guides/agents-api/observability)和[token计数说明](https://developers.openai.com/api/docs/guides/token-counting)，cached是input子项，reasoning是output子项。context-mode固定90af14e的parseCodexUsage移植保留ELv2/MODIFIED，但修正重复相加、缺值补0、累计求和和第二行游标假设；同计数物理事件保留原生身份，读最新已报告累计。

## 本机应用

R升级前备份.context-rail/backups/before-accounting-stage-20261023T121929266929Z.sqlite3，回执accounting-backup-receipt.json。原M通过app/control.ps1 stop并等PID85824退出，备份原库/受影响模块至原service/backups/accounting-stage-20261023T122801455716Z；仅部署native_event_stream.py、context_mode_usage.py、learning_origin.py，再原control.ps1 start。回执.context-rail/accounting-deployment-receipt.json。Hook/原生trust未改。

## 实际任务链路

.context-rail/scripts/verify_accounting_stage_live.py实际冻结git status --short的stdout/stderr/时间，交原OutputRecorder保留全流并返回2048 bytes内投影，保存ACCOUNTING_USE.md revision1并刷新Bridge。R version76→80、schema10。既有Codex reader真实官方MCP完成brief和2次各1200原文bytes的guide回读。

首次verifier在汇总时对非get计量ref=null处理不当，业务调用与计量已保存。修正该报告脚本后，resume_accounting_stage_live.py从实际状态继续，未重做capture/put：4项工具记录跨新MCP进程保留，已知回读2400 bytes。capture和guide是不同来源，未声称该两次guide回读由capture摘要引起。请求账本此时0行只表示未记录宿主最终请求，不表示未发生模型请求；provider输入削减和费用收益均null。

原监听器部署后，以原Store.ingest和原cursor对明确选定的真实Codex rollout做一次增量协调。3份真实usage报告可从新metrics读到，取最近已报告cumulative，不求和；exact source-event-v1:10b28bdafe6250f9b7e21caa2716ba72经原get_event回读与报表一致，来源时间2026-10-23T12:30:01.598000+00:00。模型名在该来源状态未观察到，reported_model=null，未从本聊天模型设定补造。旧token_count未默认回填，native usage不归到未知Rail/provider请求，也不计算money。

真实只读efficiency因未来轮数/成本缺失返回insufficient_data、model_calls=0；不会拿原生累计或小返回包猜未来收益。这个调用是阶段演示提案，不是系统已观察到预算压力的证明。

SDK接线说明定稿后，finalize_accounting_guide.py显式保存指南revision2，R version80→81，当前snapshot=68ecc6a3bbd048c1a30f8ef9e84acf53（固定base=a558284943e241eab297739aca0864f2），active pointer/query binding已更新。新revision末尾2000 bytes精确MCP回读，当前Bridge源核对通过；第一条实际链路snapshot25f754e79b0e4472aab058bcc5d1554d保持历史证据。完整回执.context-rail/accounting-stage-live-receipt.json complete=true，final_guide记录最终状态。

## 收尾

最终wheel已成功构建，严格OpenSpec与tracked diff --check通过；conda可选入口缺conda.auxlib.packaging是非致命旧提示。未新增默认后台、API调用、付费请求或模型服务。旧MCP目录不会热加载新工具，重连原配置才看到metrics/efficiency；当前桌面旧连接是否已刷新未观测。
