# E08 验收（2026-10-23）

E08已完成，累计26/72；C06统一查询继续下一批。时间线复用原R/M库及原监听器，context-mode错误分类已实际移植（ELv2），ai-memory范围/排序/分页逻辑作为适配参照。

## 当前行为

`.venv/Scripts/python.exe -m unittest tests.test_event_timeline`最终13项GREEN，5.364秒。仅执行本批用例，未跑旧套件/上游测试/全项目回归或smoke、health、hash、import/startup探针。

有效RED：缺context.timeline和M timeline入口；空stderr被误判不可用使前次尝试消失；跨native session复用call_id发生文件串联；超限旧源在排除前加载正文；真实Codex exec子结果退出码被外层unknown掩盖。分别修复后GREEN。CLI夹具遗漏stdout字段的错误先修正，不算实现RED。

覆盖乱序时间/时区/未知时间、错误文件过滤及邻域、源失效仍见缺口、task/session隔离、成功输出错误例子不判失败、实时分页及完整响应预算、旧R/M元数据pending→有界补建且版本不变、真实CLI/官方MCP ClientSession查询与原文回读。exec聚合只分类失败子结果，并保留原始native状态/各退出码。

wheel成功；conda可选入口缺conda.auxlib.packaging仍为非致命提示。严格OpenSpec valid。最后针对exec信封的新改动后再次构建，结果以执行回执为准。

## 部署与真实资料

原control.ps1 stop并等旧pid退出；原R库schema8→9，只种pending元数据，随后显式补建指定task；原M新事件同步索引，旧事件按指定workspace/client/session补建。每次均备份R/M库与15项app增量，再原control.ps1 start。

初次备份时间20261023T063917848156Z：原service backups/before-timeline-*.sqlite、项目.context-rail/backups/before-timeline-*.sqlite3；app capture-v1-20261023T063918344237Z。初次完整回执timeline-first-deployment-receipt.json保留schema8基线。

真实资料显示Codex外层error_state常为unknown，但已观察exec input_text JSON中有完整chunk_id/wall_time_seconds/exit_code/output。冻结当前新行为RED后增加一层结构识别，派生索引升级timeline-v2-context-mode-90af14e，重建旧派生条目；原始事件不改。

最终备份时间20261023T064255351175Z；app capture-v1-20261023T064255739621Z，回执.context-rail/timeline-deployment-receipt.json。当前task self-use-01a161c7索引4项（1工具结果、3显式原生事件）；原生Codex会话01a161c7-38d2-7333-a025-9d0be8d6a62a索引228项、pending0。此覆盖只代表已捕获来源，不代表全部历史日志均被补读。

任务version74→74，当前snapshot仍75891fb8b28d446999ef355f73ba1ef0，owner/epoch与原证据保持。没有将原会话所有事件自动灌入任务。

实际通过现有Codex reader绑定启动官方MCP stdio客户端，context.timeline查询3条原生事件，顺序与actual occurred_at一致；context.get回读source-event-v1:a9cd3019a1bd55118ff39548ce59398d，确认含“直接下载”。

原M实际查询test_failed，定位source-event-v1:6edf632e897b53bf82cf659c96986f85，发生于2026-10-23T05:19:33.924000+00:00，原error_state=unknown、真实exec退出码[1]；around返回前后5条事件，完整原证据可回读。详情.context-rail/timeline-live-receipt.json及scripts/verify_timeline_live.py。

## 边界

错误类别是已声明失败后的文本启发式，不是因果证明；未提供状态的未知工具输出不猜失败。未结构化路径不自动关联。offset是实时页，写入/补建后需留意index_highwater/task_version变化。来源缺口、未知时间、正文省略都可见。

旧长期运行MCP进程不会因源码变更自动获得新工具目录；本次新入口由真实新stdio会话验证。客户端重新连接同一既有配置后可发现context.timeline，无新增MCP服务器或trust步骤。
