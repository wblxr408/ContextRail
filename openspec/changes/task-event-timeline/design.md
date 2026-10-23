# Design

## Context

E01原M normalized_events及Rail显式import body可回读；task内也有tool-result manifest。当前list按capture sequence排序，C03索引是全文跨度，不保存时间/会话。沿用原Python/SQLite，无新增依赖。

## Goals / Non-Goals

完成E08并提供C06需要的事件查询契约。当前task只查询已显式归入任务的来源，原M单独精确workspace/client/session读取，不从模型参数打开任意memory home。时间线表达经历先后，不推断解决关系。

## Decisions

1. 原R库schema9及原M库增量timeline_entries/timeline_state/timeline_paths，canonical timeline_index.py共用派生规则。新写入与原证据同事务索引；旧库只标pending，显式bounded reindex，避免每次查询读全部blob。原任务版本、raw正文与事件身份保持。
2. R入口context.timeline及timeline/timeline-reindex CLI绑定scope/session；原M timeline/timeline-reindex沿用stream精确来源边界。原文引用分别是context.get artifact ref与原stream-get选择器。源不可读保留失败而非空结果。
3. 时间归一UTC，未知时间置后，稳定同时间顺序按索引序号；offset分页是实时视图，报告读视图代次/不保证跨写入稳定。围绕精确id定位排序位置后返回前后窗口。时间边界有时区、半开区间，范围过滤排除无时间事件。
4. 文件只提取来源明确的source_paths、结构参数path/file_path等，不从输出文字猜文件。工具结果通过同call_id的已捕获调用继承文件/工具来源；不打开这些路径。
5. 直接移植context-mode 90af14e error-classifier.ts::classifyError的10类有序规则，保留ELv2/MODIFIED说明；只对来源已声明失败/超时执行，标heuristic。ai-memory 1bd8663 session_observations_scoped启发范围先过滤再排序分页，不复制Rust pool/权限全栈。
6. 完整序列化预算，超小预算报错，省略预览仍留引用；每页最多100、显式补建最多100件/16MiB，单件超界明确excluded。覆盖统计与最多10条来源缺口有界返回。

## Risks / Trade-offs

原生日志未说明失败→error category保持未知，不把含error的正常文本当失败；普通未结构化文件/嵌套脚本路径不自动关联→用户可原文搜索。late arrival会改变下一实时页offset→响应明示live view；并发读页在单一数据库读事务内一致。

## Migration Plan

当前新行为TDD通过后备份R/M库及app，原生增量迁移并显式分批补建当前task/指定会话，再部署原监听器。验证实际时间顺序与原文回读，更新E08。C06跨源统一查询另批推进。
