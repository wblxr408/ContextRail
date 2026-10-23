# 既有工程记忆服务的增量适配

入口源码来自本机已安装工程记忆app，在项目中保留可审阅版本。listener.py、history_import.py、memory_runtime.py接入F04；不包含app/vendor、配置、模型、队列或新守护进程。

开发时capture_sanitizer.py引用canonical contextrail/sanitization.py。deploy.py将该实现和固定上游规则合成安装目录的独立模块，并附MIT LICENSE和来源说明。以后改策略应修改canonical文件再部署。

部署前用既有control.ps1停止EngineeringMemoryListener并等待退出，执行deploy.py，再用原control.ps1 start。备份在既有服务backups；deployment.json记录受影响文件和部署前是否存在。恢复对应文件后由既有入口启动；不恢复整份数据库或改写用户配置。

F01已增加canonical source_events.py → 安装event_identity.py，复用原state.sqlite保存来源认领/别名/冲突。新job附_event，旧job ID与事实引用不变；只读events入口见[事件身份指南](../../EVENT_IDENTITY_USE.md)。

E01已增加canonical event_stream.py → 安装native_event_stream.py，复用原游标事务保存原生事件；context_mode_tools.py及ELv2一并部署。stream/stream-get只读入口及Rail显式导入见[事件指南](../../NATIVE_EVENTS_USE.md)。

F02/F03增加canonical capture_policy.py与learning_origin.py；部署时生成独立模块，并同步memory_integration.py的事实来源接线。归属认领、无正文委托和学习投影复用原库与监听器，见[来源指南](../../ORIGIN_USE.md)。没有新增默认Hook或重复学习入口；F08完整轮换另行实现。

E08部署独立timeline_index.py及ELv2 context_mode_errors.py，复用原事件库派生时间/来源索引。timeline/timeline-reindex按精确workspace/client/session浏览/有界补建；见[经历指南](../../TIMELINE_USE.md)。旧R库升级schema9前需一致备份，旧资料pending明确，原事件正文不改。

C06/C08/H01作为一个阶段部署：memory_runtime.py提供原目录query_revision与opt-in recall版本/时间精度；mcp_proxy.py只给原内部runtime路由增加该只读目录操作，不增加公开MCP目录。普通recall不附整个catalog token。统一查询/重用/状态位于R适配层，见[阶段指南](../../QUERY_USE.md)。

F05–F08作为一个阶段接入capture_rules.py、ingest_delivery.py、stream_cursor.py和原listener/history_import。原库新增delivery/control/源代/别名，原循环有限drain和共享退避；CLI提供交付、取消与内容无关状态。learning_origin v2排除新管理页。使用与观察限制见[阶段指南](../../CAPTURE_DELIVERY_USE.md)。
