# 采集归属与学习来源

F02/F03复用既有EngineeringMemoryListener及其state.sqlite。原生Codex/Claude JSONL的唯一capture_owner是`existing_jsonl_listener`；显式资料导入由`existing_import_queue`负责。入口代码设置producer，消息正文自报的owner不改变归属。

## 可选工具Hook

`listener.py capture-hook --client codex --hook-event PostToolUse`接收stdin原生JSON信封（`cwd`、`session_id`、可选`turn_id`或`tool_use_id`），只写无正文委托回执，不另外采集工具正文或产生知识job。Claude把client改为claude。此批未新增默认Hook配置；原UserPromptSubmit召回/任务注入继续使用现有入口。

例如在需要诊断的显式入口中使用：

```powershell
$hookEnvelope | C:/Users/wblxr/AppData/Roaming/uv/tools/basic-memory/Scripts/python.exe C:/Users/wblxr/.codex/services/engineering-memory/app/listener.py capture-hook --client codex --hook-event PostToolUse
```

回执`status=delegated`、`delivery=not_observed`只说明委托策略，不能证明监听器在线或已经保存。真正保存的证据使用E01 stream/stream-get回读。重复委托增加deliveries，不新增知识效果。

```powershell
C:/Users/wblxr/AppData/Roaming/uv/tools/basic-memory/Scripts/python.exe C:/Users/wblxr/.codex/services/engineering-memory/app/listener.py capture-status --workspace D:/ContextRail --limit 20 --offset 0
```

只读输出有界owner分页和最近委托/导入回执；它不是服务存活检查。

## 历史保留与学习投影

完整native-context/Bridge/event投影、原生isMeta/isCompactSummary、保留的注入块、codex checkpoint和工程记忆生成页不进入自动学习。显式导入生成页保留原source container和history_only回执，不创建part jobs。原生历史仍保存为E01事件及filtered候选，能够继续精确回读。

`<system-reminder>`、`<contextrail-history>`和`<codex_internal_context>`内的注入正文从学习视图排除；块外的新用户回应保留。代码围栏中的示例、普通项目讨论和仅自报origin的普通JSON不因关键词被排除。未标记、改写成普通文字的历史不在规则可确认范围内，仍由现有分类器判断。

job.user/final及F01正文绑定保留处理后原文。分类器、引用验证和事实保存使用同一内存学习投影，`range_basis=learning_projection_not_raw_offsets`明确其位置不能当作原文偏移。旧queued/retry在模型预算预留前重评；旧ready_to_save若投影改变，保留旧结果审计和调用费用后重新分类。已经保存的事实继续原索引核验。

Bridge/native包、原生事件、自动笔记、事实和生成页携带`source_event`、`derived_from`、`history_origin`。这些字段表达来源关系；当前任务的完整要求仍由Bridge原账本确定。

本批12项确定性行为用例位于`tests/test_capture_origin_policy.py`，只针对当前归属/来源契约。实际部署与本机验证见`openspec/changes/capture-owner-and-learning-origin/verification.md`。
