# Design

## Context

F01/F04已部署。实际Codex记录有message.id、phase=final_answer、function/custom tool call/output及call_id、ordinal；Claude user记录同时承载tool_result，assistant内容可含tool_use/thinking。原reduce_record只在回合完成时产生学习候选，raw过程没有持久化。

## Goals / Non-Goals

完成E01可恢复证据流及可信Rail导入；原学习候选仍按现有完成回合和meaningful规则，raw事件本身不发模型请求。不增加默认Hook采集入口或观察器。F02唯一producer治理、F03派生材料过滤、F08完整轮换、E08时间浏览和C06统一查询继续独立。

## Decisions

1. canonical event_stream.py使用纯函数归一化，和原Store.ingest同一事务调用；只在原state.sqlite新增normalized_events元数据，body复用F01 source_events.content中的处理后容器，不复制为新知识库。上下文仅新增小型_stream状态以关联原生turn，不把旧turn强塞给未知新请求。
2. 每个观察事件namespace包含client及event_type，原生message/uuid/call/turn优先，缺身份用F01 source_position。Claude工具结果独立于真实user文本，助手文本块未知final性时明确assistant.message；Codex final/final_answer均识别。工具call/result用call_id关联，不根据输出里含error一词推断执行失败。
3. 直接移植context-mode 90af14e extract.ts的TOOL_NAME_NORMALIZE与normalizeHookInput语义，保留original_tool及ELv2许可/修改声明；不引入Node后台或其语义分类规则。F04先处理完整数据，再生成有界预览。原生事件body保留完整可采集文本/参数/结果，thinking、images等按块声明省略；4MiB处理界限延用F04，超限保存明确omitted记录。
4. metadata包括role/type/native IDs、nullable occurred_at及source_time_state、独立observed_at、源JSONL路径/代际/行区间。时间只接受有时区ISO；缺失/无时区/无效不编造。原始JSONL指针是出处，不冒充可在轮换后永久读回的字节ref；持久化处理版不依赖后续原文件存在。
5. 本地stream/stream-get以workspace/client/native session显式查询原M库，event id精确回读并按自身UTF8 bytes分页。列表以采集sequence分页，不宣称按真实时间排序。重复同身份正文变化进入F01冲突，不覆盖首次证据。
6. Rail event-import是可信CLI，只读打开选定M库，绑定项目实体workspace和显式native session/client，有界读取、owner/epoch/freeze原子写入body及manifest。使用稳定事件名与完整处理版，重复导入相同事件不增revision；已有不同内容拒绝。manifest为未来事件桥保留原生ID/角色/时间/来源；现有MCP get/search直接回读。默认每页20、至多100事件，累计stage最多16MiB（覆盖4MiB文本经JSON转义后的证据），超预算给继续游标；无模型写工具。

## Risks / Trade-offs

旧游标已越过的历史没有raw事件→仅新记录默认增量，历史明确backfill，不假装已全量重建。缺turn信息→保持unknown，不能串给前一回合。源日志轮换→处理版独立可恢复，原路径仅出处；完整轮换另属F08。原生格式新增字段→白名单隔离reasoning并保留unsupported/omitted边界，按实际新字段补适配。

## Migration Plan

真实临时队列消费者先RED，覆盖崩溃重开、工具/回复隔离、来源脱敏、时间缺失、原子回滚与Rail回读。新schema原Store增量创建；不移动旧job或自动补录全部历史。备份原app及SQLite后经原control.ps1停止/部署/启动，再使用真实native日志的有界明确补读验证请求与工具分别可回读，前台不调用模型。回退保留新表和旧引用，不破坏已存证据。
