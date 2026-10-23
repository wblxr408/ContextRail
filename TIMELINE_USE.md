# 按发生时间浏览经历

E08复用原R/M库，把任务工具结果和已显式导入本任务的原生事件编成可回读时间线。新证据在原写入事务同步建索引；旧资料只标pending，需要显式分批补建。

## 当前任务

```powershell
.venv/Scripts/python.exe -m contextrail timeline --workspace D:/ContextRail --task self-use-01a161c7 --session host-01a161c7 --limit 10
.venv/Scripts/python.exe -m contextrail timeline-reindex --workspace D:/ContextRail --task self-use-01a161c7 --session host-01a161c7 --epoch 0 --limit 100
```

同一入口通过MCP `context.timeline`提供；task/scope在宿主绑定。`native_session`、`role`、`origin`（native_event/tool_result）、`file`（工作区相对路径）、`error_category`及`since/until`可过滤。来源先受task/session限制，再排序分页。MCP不接受磁盘路径或memory home覆盖。

错误分类包括file_not_found、command_not_found、edit_match_failed、test_failed、syntax_error、runtime_error、permission_denied、git_conflict、timeout、unknown。规则实际移植自context-mode，只对来源明确失败/超时分类；成功输出引用错误文字不当作失败。classification字段说明启发式来源和最多64KiB文本窗口，原status/exit_code始终保留。

Codex外层缺is_error时，还识别已观察到的exec结果信封（chunk_id/wall_time_seconds/exit_code/output）。只解析工具输出块的一层完整JSON，不递归把程序stdout里的示例JSON当新执行；保留native_error_state、各exit_codes和outcome_basis。聚合中任何已声明非零会标failed，分类只看失败子结果，运行未结束则标outcomes_incomplete。

先用error_category找错误，取返回的精确id，再请求`{"around":"返回的id","limit":5}`查看前后尝试和纠正。around与非零offset互斥。每项ref直接交给context.get，不把preview位置当原文偏移。文件来自source_paths或工具结构参数；同call_id结果可关联调用文件，关联受来源/客户端/会话限制，不打开这些路径，也不从日志文字猜文件。

## 原记忆库的精确会话

```powershell
$memoryPython = 'C:/Users/wblxr/AppData/Roaming/uv/tools/basic-memory/Scripts/python.exe'
$listener = 'C:/Users/wblxr/.codex/services/engineering-memory/app/listener.py'
& $memoryPython $listener timeline --workspace D:/ContextRail --client codex --native-session 01a161c7-38d2-7333-a025-9d0be8d6a62a --limit 10
& $memoryPython $listener timeline-reindex --workspace D:/ContextRail --client codex --native-session 01a161c7-38d2-7333-a025-9d0be8d6a62a --limit 100
```

M入口支持`--file`、`--role`、`--error-category`、`--since`、`--until`、`--around`。返回ref包含stream-get选择器；用原stream-get及字节范围打开处理后完整事件。读取来源库失败返回timeline_unavailable，不变成空的成功经历。这里是原生会话边界；不会自动归入当前task，归入仍需既有event-import。

## 时间、分页与缺口

occurred_at归一UTC并升序，无来源时间/无时区/非法值保持null及明确状态，置于有时间项之后。观察时间不代替发生时间；工具结果按finished_at排序，started_at另存。since包含、until不包含，必须提供时区，设置时间范围后未知时间不参与匹配。

offset/next_offset是实时分页，不保证写入或补建发生后的页间稳定；index_highwater及task_version帮助识别变化。单页读取在一致事务中完成。同时间按索引序号排序，仅表示已观察顺序，不能推断因果。

coverage明确pending/excluded/unavailable，complete只指当前选定来源的索引覆盖，不代表整个会话/历史都曾捕获。任务范围仅为已归入的工件。原文失效仍显示availability缺口，不伪装成可回读。最多100项/完整响应64KiB，预算不足先省预览/路径展示，仍放不下引用则明确失败。

补建最多100件/16MiB，单件超过4MiB只按元数据记录排除；不自动全量扫描blob，不改任务version或原始证据。迁移到R schema9前备份，旧R先种pending，M按已捕获raw事件计算pending。应用旧版本代码前需相应版本库/升级策略，不能让旧schema8写入器直接写schema9。

本批消费者用例tests/test_event_timeline.py；实际资料验收见openspec/changes/task-event-timeline/verification.md。
