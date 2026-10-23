# E05 / E06 / I02 阶段验收

2026-10-23，集中实现恢复意图、精确会话映射和显式领取。权威TODO累计38/72，持续实现goal仍active。

## 当前行为与验收

tests/test_precise_resume_stage.py初始11项均观察预期RED：恢复丢失末尾纠正，错误窗口/已被替代要求未被拒绝。冻结整阶段后一次运行13项：10项首轮GREEN，3项失败；修复binding事件字段与Store._event(scope)参数冲突、首次offer的tuple与持久JSON list不一致，并修正用例误读已有text字段，三项定点GREEN。未重新运行完整13项。

另对真实应用发现的内联上限、原owner的管理页投影、迟到ack及缺版本候选各加一个当前缺陷消费者，分别观察RED后定点GREEN；共17个独立消费者有对应GREEN记录，不声称最终完整17项重跑。覆盖长消息末尾、UTF8/quote范围拒绝、supersession、两任务隔离、未知会话、精确绑定重送、只读预览、送达/目标/逐项回执拒绝、原库重开、中断续接、6个真实并发接收、两个重试强制错开至激活后的迟到ack、CLI、真实MCP、原M实际JSONL入库和投影。

强制交错暴露原ack在另一重试激活后抛旧lease错误。适配层只在同offer/同回执且原协议已前进时重新核对来源，再继续原activate幂等路径；没有吞掉未前进的失败，也没有新增执行器。缺版本时原实现返回空候选，补为本scope有明确不匹配标记的候选，selected保持null。

未执行旧阶段/全项目回归、上游套件、smoke/health/import/startup/hash探针。

## 精确与覆盖边界

原Bridge校验器仍验证账本、revision和替代链。新增resume_intent只接宿主明确标注的角色/字节窗口；最新请求和纠正必须active，original_goal可superseded。完整处理后用户源保留为冷证据；START、对应原文窗口和当前要求必需，完整清单可按精确ref补读。next_action取host声明的当前请求statement，不是自动理解所有历史或自动生成下一步计划。

候选和preview不领取；native映射复用R events、Hook准备回执，绑定scope/snapshot/version/client/session，不用项目latest。offer/accept只面向可信host，不新增模型写工具。交接使用原prepare/acknowledge/validate/activate/abort和epoch；逐项语义和送达为接收方/host attestation，不证明模型消费。Rail lease不替代Bridge workspace writer claim。外部文件仍有独立写入边界，本阶段不声称文件与SQLite跨资源原子性。

旧Bridge包intent为not_declared；新可写恢复要求先声明意图。未知/页外历史保持unknown，候选新鲜度直到preview才核对。

## 实际本机应用

原M精确用户事件`source-event-v1:bd3e95e676e6554bad724bfea25a1fd6`，workspace D:/ContextRail、Codex session01a161c7-38d2-7333-a025-9d0be8d6a62a，处理后全文6314 bytes。它是goal envelope，宿主明确选择其中用户提供的objective数据，不把整个内部上下文当用户规则。末尾上传要求在该文本321..363，42 bytes；保留完整源与原事件引用。

在原revision-1基础生成新要求：R001 rev2 superseded_by R005，R005为整阶段验收及上传的有效goal，R006为上传纠正；R002/R003原约束保留，R004 rev2仍pending。revision-2是首次应用时的历史发布；随后为内联表示修正发布revision-3，旧包不改写。生成前R一致备份`.context-rail/backups/before-precise-resume-20261023T130646221136Z.sqlite3`。

实际脚本第一次在load_project参数错误处终止，修正为overrides并从已保存发布继续；随后真实Claude处理器暴露上限，精确源不重捕获，保留旧发布并产生revision-3。新包基准snapshot`9312c1032756423e9aac7dfcb3c9a395`；最终guide保存后的当前snapshot`3ebaf893bcd944a2ae73e1a944addcb8`，task version117，schema仍10，owner仍host-01a161c7 epoch0。registry仅更新base指针/context预算，query binding与active pointer更新；Hook命令与trust未改。

原生Hook引用视图减少重复清单与格式说明、冷目录只显示1项并保留完整分页入口；收缩可选页，必需START/纠正/原文仍在。实际Codex处理器返回20758 bytes、15188 UTF16单元；Claude返回13111 bytes、9935 UTF16单元，未超过原10k内联限制。原生Codex的additionalContextLimit是近似token门槛，不能和字符数相等；2026-10-23用OpenAI Docs实读[官方Hooks说明](https://learn.chatgpt.com/docs/hooks#large-hook-output)。本次没有测provider tokenizer或重新声称native model消费。

Claude的原M调用返回timed_out，未伪造main注入成功；Codex main继续由独立原生Hook提供，本机调用记录为未观察。两个configured handler均实际返回当前任务包，只证明处理器输出。实际候选6项保持selection_required、selected=null，owner/epoch不变。

官方MCP SDK从既有reader配置启动并精确回读上述42 bytes和START中的新纠正。完整机器回执`.context-rail/precise-resume-live-receipt.json` complete=true，final_mcp记录精确refs。原active-native-client/query-sources/registry指向最终版本，没有领取当前正在工作的聊天或其他task。

## 原学习owner与交付

新增管理输出使用既有learning_origin投影，实际旧安装对新候选页返回eligible=true，观察RED后备份并仅部署learning_origin.py。原control.ps1 stop并等待实际PID57560退出，备份原库/模块至原service/backups/precise-resume-origin-20261023T131255130233Z，再原control.ps1 start。部署回执`.context-rail/precise-resume-deployment-receipt.json`。新管理页不进入学习正文，显式new_user_response仍保留。用例的Windows子进程stdin最初编码不匹配，明确-X utf8后定点GREEN；没有把乱码当成功。

最终wheel构建成功，严格OpenSpec与diff格式检查通过；旧conda可选bdist_conda入口缺失提示不影响构建。未新增默认观察者、记忆库或聊天；新恢复/领取代码不调用模型，原M检索仍走既有入口和预算。模块已独立提交为[715dfdbf](https://github.com/wblxr408/ContextRail/commit/715dfdbfa0ff038b44c8181fdfb508b6efb2b158)，git push origin main成功，GitHub connector已读回该commit。交付状态记录作为后续文档提交，不改变本模块行为。
