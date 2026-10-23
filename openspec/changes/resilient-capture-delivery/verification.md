# F05 / F06 / F07 / F08 阶段验收

2026-10-23，权威TODO累计42/72。本阶段完成后按用户要求上传模块，持续goal保持active。

## 当前消费者与结果

tests/test_capture_delivery_stage.py冻结整阶段契约，基线观察到排除正文已持久化、同尺寸替换漏读及额度耗尽available未后移。集中实现后一次16项全部GREEN（18.970s），未重新执行全项目/旧阶段套件。

随后只补当前实际缺口：旧安装处理器模型回复后把取消请求当skipped、旧学习owner把新队列管理页当正文，两项先RED，最小部署后定点GREEN；敏感交付ID被拒绝却在响应中回显，观察RED后改为安全ID/null，定点GREEN；共享资源失败只退单项、另一积压仍调用，2变1的实际调用边界RED后加入持久judge/index退避，定点GREEN。累计20个独立当前消费者有对应GREEN，不声称最后全20重跑。

同阶段两个已测消费者按实际边界扩充后定点修复：取消后stage仍cancel_requested/waiting，统一为cancelled并清除活动请求；offset0过长半行误报caught_up，改为显式pending。最终安装处理器的协作取消也在最终部署后定点GREEN。未运行上游套件、旧回归、smoke/health/hash/import/startup探针。

覆盖：项目策略在source_events/normalized_events/索引/cursor/派生job前生效、显式用户文本独立、未知工具/无效配置保守处理、A01同一配置契约；inbox重开/逐项失败/重送/冲突/无正文Hook；原额度拒绝无attempt/调用、资源重试/共享退避跨重开、精确取消/在途回复后停止保存；替换、半行、旧代末尾、新文件先发现、非jsonl扩展别名、事务失败回滚、过长记录后继续；实际CLI状态。

## 实现边界

capture_rules.py用已知直接字段与封闭元数据，不读取工具路径文件或解析任意shell；file ignore不等于语义秘密识别，用户文本独立选择。skip仍保留政策排除坐标，原宿主日志/既有历史不自动改写，R可信宿主明确保存的原文继续原契约。相同ID在政策改变后重放有既有冲突边界，不宣称旧正文已经被清理。

inbox与job_control、源代/别名均在原M state.sqlite。确认分durable inbox、原import queue或delegation-only，不等于学习完成或原生正文已捕获；原JSONL owner不变。共享退避按judge/index阶段持久保留，额度拒绝不消耗模型attempt；在途模型不强杀，saving太晚不能保证撤销外部效果。原calls及审计保留，失败不按TTL/次数/容量删除。

原游标使用原canonical_path；文件身份、epoch和别名与原事件/job同事务。旧无身份游标明确unverified，不默认回放所有历史。未观察的原地截断后增长到更大尺寸与普通追加无法仅靠元数据区别，未知/无法绑定移动保留缺口；不宣称任意日志行为无损，不构造hash探针。

## 本机部署与受控应用

原control.ps1 stop并等实际PID116772退出，先备份原库/模块至原service/backups/capture-delivery-20261023T135020053731Z。选择部署listener/history_import/capture_rules/ingest_delivery/stream_cursor/native_event_stream/learning_origin/NOTICES共8文件，不覆盖config、resource broker、模型或MCP目录。后续取消字段修正备份capture-cancel-state-20261023T135745979518Z；最终共享退避/offset0修正部署备份capture-delivery-20261023T140836070568Z，所有回执保留在capture-delivery-deployment-receipt.json。

原调度入口为pythonw的engineering_memory_lifecycle_entry.py，原任务MultipleInstances=IgnoreNew。途中status.json仍指旧PID且一次启动请求返回800710e0，未以此推断守护进程停止/成功；按实际pythonw入口发现102036/131228两个进程句柄（原启动链），原stop后等待其退出再更新，最终仍通过原control.ps1 start。没有注册新任务/后台，也没把旧PID状态当验收。

本机受控workspace为.context-rail/live-capture-delivery/20261023T135336158476Z。通过已部署原Store和Service.moved/flush处理Codex格式来源，明确是受控前台来源而非实际原生客户端消费：9条事件，忽略Read参数/结果及派生助手正文未进入该workspace的持久记录；显式用户、新会话与旧代最后一行均读回。alias原名native.jsonl→native.jsonl.1，新文件先被采集仍恢复旧末尾。

原库3项delivery（public document/private document/无正文Hook）全部确认；重送3项duplicate。只产生1个受控public import job，私密内容没有入队，Hook仅委托。停止原后台期间对该唯一验证job明确延期、取消，保留来源及审计，受控执行期间calls账本不增加。最终真实queue-status显示state/stage=cancelled、cancel_requested=0、pending=0、3项delivered。未取消或改动其他用户job，不把该受控0调用外推为整个后台没有模型活动。

learning_origin提升v2，新管理输出不学习，显式回复沿用原机制；旧混合结果只有视图改变才按原重判路径处理。原独立监听器与既有预算继续拥有模型工作；本阶段不保证provider连接或原M冷查询延迟已解决。

## 任务材料与交付

CAPTURE_DELIVERY_USE.md已保存到原任务guide revision2，fact/task version118→119，R schema仍10、owner host-01a161c7 epoch0。当前snapshot1ce114448b14438c88abf6718f579c96，base仍9312c1032756423e9aac7dfcb3c9a395。active/query binding更新，Hook命令/trust未改；原用户目标和最后上传纠正仍有效。

既有Codex reader的官方MCP精确回读5525 bytes指南，当前Bridge source_current=true。备份.context-rail/backups/before-capture-guide-20261023T141144393246Z.sqlite3；完整机器证据capture-delivery-live-receipt.json complete=true及rail字段。第一次guide revision1/snapshot6939a0406afc444d86679edabd893d09是历史状态。

最终wheel构建与严格OpenSpec校验通过，构建日志保存在本机.context-rail/capture-delivery-final-build.log；未发布dist或改变用户既有evaluation/research改动。diff格式检查及模块独立提交上传在交付收尾记录。
