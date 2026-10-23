# F02/F03 验收（2026-10-23）

完成唯一采集归属与自动学习来源投影，累计25/72。使用原M库、监听器和资源预算；capture-hook只作无正文委托。

## 当前行为证据

`C:/Users/wblxr/anaconda3/python.exe -m unittest tests.test_capture_origin_policy`：12项通过，最终5.618秒。有效RED包括恢复包产生queued、Claude新纠正丢失、生成页重新入队、缺capture-hook命令、事实/包缺来源链、过滤导入来源链未保存；修复后GREEN。

覆盖Hook/监听任意先后与重复、自报owner无权覆盖、回执无正文；恢复仍保留raw、块外纠正分类、普通讨论/围栏示例不误删、原生compact/checkpoint排除；旧retry在预算前过滤、旧ready审计后重新分类、分类器只看当前纠正而原文完整；事实/页来源与真实Bridge/native序列化。

新增CLI夹具最初PYTHONPATH不完整，修正环境后才观察缺命令RED；native夹具scope首次错误，修正后才观察缺来源字段RED。这些夹具错误不算实现RED。

离线wheel构建成功；conda可选入口仍报告非致命conda.auxlib.packaging缺失。严格OpenSpec通过。未执行旧套件、全项目回归、上游测试或smoke/health/hash检查。

## 本机部署及实际资料

原control.ps1停止并确认旧pid已退出后，备份、替换13项增量，再由原control.ps1启动：

- SQLite：原服务backups/before-origin-policy-20261023T060310452420Z.sqlite。
- app：原服务backups/capture-v1-20261023T060311101735Z，deployment.json保留原文件清单。
- 本项目回执：.context-rail/origin-deployment-receipt.json。

实际验证task self-use-01a161c7、snapshot 75891fb8b28d446999ef355f73ba1ef0的Bridge包，冻结为.context-rail/evidence/f03-current-bridge-packet.json。原队列显式导入首次0、重放0学习job；source container存在，正文等于F04处理后的完整输入，history_only回执携带source_event/derived_from/history_origin。导入归属existing_import_queue。

真实Codex会话01a161c7-38d2-7333-a025-9d0be8d6a62a已有工具call_64f3d8bdc23d4b5f908439c1dfe46901；安装后的capture-hook两次接收其标识，均委托existing_jsonl_listener，jobs增量0，delivery=not_observed。委托不代表工具正文已送达。

原监听器实际继续采集同会话，观察事件source-event-v1:39d44a7d94fe5948b72190901e06e644带capture_owner及native_event_evidence来源。这是原进程实际采集结果。详情见.context-rail/origin-live-receipt.json。

本机脚本最初误用默认task scope，在Bridge读取时停止，改用原registry绑定；随后正文断言误按未处理输入比较，修正为约定的F04版本。已创建来源容器/过滤回执保留，最终deliveries=4，未产生知识job。没有更改原文或身份以使断言通过。

重启后Basic Memory原生read_note实际读回既有E01 checkpoint。真实新纠正的投影由临时原队列与确定性分类器消费者验证，未把人工测试文本混入个人长期知识；未执行付费原生Claude会话。

## 适用边界

过滤识别完整已知结构、可信原生标志、保留注入块和管理页；无法识别所有改写为普通无标记文字的历史。手动检查点保存仍可用，已保存事实不追溯删除。来源关系不代表原文偏移。

Hook未自动安装，原UserPromptSubmit召回与已信任项目处理器使用原配置。回退恢复备份内源码，保留原库和增量审计表；不全库回滚已保存事实。
