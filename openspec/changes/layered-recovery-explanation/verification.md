# D01 / D05 / H02 阶段验收

2026-10-23，集中实现分层简报、逐来源loss、加载解释。用户要求较大阶段统一验收；整体持续goal保持active。

## 契约与当前行为

新契约tests/test_layered_recovery_stage.py先写后实现。首次基线夹具未创建库父目录；修正后6项在缺context.brief/CLI brief处出现预期失败，另两项Bridge scope/动作终态夹具值修正，不把基础设施/夹具问题计为实现RED。

集中实现后一次运行整阶段9项：全部通过（12.781秒），包括覆盖优先/精确回读、重叠依赖闭包、二进制组整体留目录、目录分页/范围拒绝/未知历史、Bridge源变更拒绝、动作/准备回执、真实CLI和MCP、新STATE独占导出、原M目录队列、实际原生Hook组合/独立路径。

随后只处理发现的具体问题：

- 原生loss以bytes计却返回truncated状态值；当前消费者加强为未知时null、已知时实际截断bytes，观察RED后修复。仅该项复验通过（1.187秒）。memory原兼容truncated布尔字段仍是原展示状态，loss字段按自己的单位计量。
- 加载解释漏带原prepared回执中的历史loss计数；新增断言观察缺loss失败后补计数投影。另为本阶段改动的事件导入结果补一个提交页/重送消费者：会话meta本身也是lifecycle，夹具总数应为3而非2，修正夹具后两项定点通过（1.016秒）。没有重新跑完整10项后声称“一次全10通过”。

收尾发现新派生类型也必须遵守原F03。新增一个当前消费者，经原队列导入简报/STATE观察RED（分别新建1/3个知识job），增加新schema/来源头识别后该项GREEN（1.148秒），两份来源保留、0知识job。夹具误用不存在的container列先修正，不计实现RED；实现中origin同名遮蔽的序列化错误也已纠正。

当前阶段共11个独立消费者得到相应GREEN证据，未声称修复后又执行了完整11项。没有运行旧批次/全项目回归、上游测试或smoke/health/hash/import/startup探针。

## 上游与集成

ai-memory固定1bd866375b01031d63db54862a133376598d3701的excerpt.rs、profile.rs元数据标签、brief_tiers.rs晋升规则移植为_vendor/ai_memory_brief.py，保留MIT/MODIFIED。Rail以完整JSON实际计量，增加精确ref目录、重叠分组连通闭包；不继承recent份额/pinned slots。详见THIRD_PARTY_NOTICES.md。

Compiler.brief/context.brief/context.explain与CLI显式brief/explain返回同一种精确视图；默认compile/continue和原生Hook仍用原整篇编译器。Bridge packet默认增加loss，显式layered=True使用分层路径。STATE只创建新文件，不修改旧bundle。目录/事件导入loss各按files/events解释提交范围，不把queued当saved。收尾只部署原M app的learning_origin.py一个文件识别新派生类型；未改Hook配置/信任。

## 真实任务验收

.context-rail/scripts/verify_layered_stage_live.py在原R备份后，将真实BRIEF_USE.md首次保存为implementation/layered-recovery-stage/BRIEF_USE.md revision1，task version75→76；此后指南示例有小幅文字更新，已保存revision1仍是生成时原件。固定base a558284943e241eab297739aca0864f2，刷新snapshot 5cb54ae0568b4cce9000845fc2b1f5a0；active pointer及query binding已更新。备份名及完整结果见.context-rail/layered-stage-live-receipt.json（complete=true）。

使用既有Codex reader配置启动真实官方MCP stdio，一次链路完成：

- 新分层简报声明9个refs，8个L2完整加载、1个L1短述，optional为5个L2+1个L1；3个required IDs R001/R002/R003全部完整加载，Bridge当前源核对通过。
- L1材料通过精确context.get字节范围回读原文；其L1展示未声称全文已读。
- 用当前真实持续要求调用原Hook路径，准备回执sequence131。独立main Hook未观察正文，memory total/selected/omitted/truncated/unknown均null。
- 同一链路context.explain能读sequence131，timeline入口实际调用context.timeline，STATE生成后解析回读其精确snapshot与loss。
- STATE位于.context-rail/layered-recovery-5cb54ae0568b4cce9000845fc2b1f5a0-STATE.md。该文件是当时的有界视图，不能冒充完整聊天历史。

回执loss计数修复后，仅通过.context-rail/scripts/read_prepared_loss_live.py实际回读该具体入口：sequence131的原生整篇装配为task refs 6完整/3留冷，Bridge active requirements 4完整/0遗漏，memory仍未知。它与新的分层简报8完整/1短述属于不同生成方式，未混为同一次模型消费；修复后结果另存回执explain_after_receipt_loss_fix。

旧长运行MCP不会热加载新工具；本阶段证明新消费者，重连同一既有配置才能发现brief/explain。没有付费模型调用或实际出站请求观测，不报告token/节省收益。

## 派生材料不再学习

通过原app/control.ps1 stop并等原listener PID48988退出后，备份原M库和learning_origin.py至原service/backups/layered-origin-20261023T120155524274Z，仅原子部署这一个模块，再通过原control.ps1 start。回执为.context-rail/layered-origin-deployment-receipt.json。

实际finish_layered_origin_live.py在同一snapshot生成带来源头的新STATE：.context-rail/layered-recovery-5cb54ae0568b4cce9000845fc2b1f5a0-origin-v1-STATE.md。通过原M当前app显式导入首次/重放queued=[0,0]，该来源jobs=0，来源container仍保留为source-event-v1:004b0c19d0d9535aa204c0d349109578；原capture_receipt为history_only且origin.eligible_for_learning=false。source_events.content只存语义正文，不冒称该表保存_origin；来源链在原capture_receipts.metadata。完整结果为live回执state_origin_import/state_path_current/explain_with_origin。旧STATE不覆写。

## 收尾检查

严格OpenSpec与tracked git diff --check通过。派生类型识别补丁后最终wheel重新构建成功，不重复运行阶段功能套件。原conda可选入口缺conda.auxlib.packaging是非致命提示；不以源码读取或文档存在代替功能证据。
