# 捕获策略、交付与原生源恢复

F05–F08复用原EngineeringMemoryListener的SQLite、事件身份、jobs/cursors与资源预算。新逻辑在contextrail/capture_rules.py、ingest_delivery.py、stream_cursor.py，原服务适配在integrations/engineering_memory。没有新默认观察者或长期记忆库。

## 项目捕获规则

在项目.contextrail.toml显式选择；不设置捕获表时保持原默认采集行为。

```toml
[capture]
ignore = ["private/**"]
metadata_only = ["generated/**"]
allow = []
user_mode = "full"
assistant_mode = "full"
unknown_tools = "metadata_only"
default_mode = "full"
```

full保留经过原脱敏的内容；metadata_only使用封闭元数据；skip只留政策排除标记和原事件坐标。规则先于原生source_events、索引、cursor context与知识job。它控制M新采集/派生数据，宿主日志和既有历史仍是独立来源；可信宿主显式保存的R原文证据继续使用其原契约。显式用户文本使用user_mode，不能把文件ignore当成所有语义秘密识别器。已知Read/Write/Edit工具仅提取明确字段，不读取工具路径中的额外文件；有规则而工具/路径未知时保守处理。Bash、搜索、未知MCP默认只保留元数据，必要时可用tool_modes显式指定。此选择也会影响该轮助手派生正文，避免从受限工具复制内容进入知识队列。请按实际项目选择规则。

结果继承同call-ID决定；无关联结果保持未知状态。无效/过大配置退为元数据并记录invalid_policy，不回显原内容。既有历史不自动删除或改写；政策改变后重放相同源ID会保留原身份/冲突边界，不宣称已清理旧资料。

## 持久补传与逐项确认

原listener.py新增delivery-submit、delivery-drain、queue-status、jobs-cancel、streams-status。均为本机可信host命令，不是模型写工具。

delivery-submit的JSON数组最多100项，每项包含稳定id、kind、workspace。document/git还带source、event_key、content、可选occurred_at；native_hook仅允许client/native_session/hook_event/可选source_event，拒绝正文。提交前验证与脱敏，accepted表示原库收件已持久化；drain才表示交到原导入队列或无正文委托，均不等于知识已学习。

```powershell
python ORIGINAL_APP/listener.py delivery-submit --home ORIGINAL_HOME --input delivery.json
python ORIGINAL_APP/listener.py delivery-drain --home ORIGINAL_HOME --ids '["stable-id"]'
python ORIGINAL_APP/listener.py queue-status --home ORIGINAL_HOME --workspace D:/YourProject
python ORIGINAL_APP/listener.py jobs-cancel --home ORIGINAL_HOME --ids '["EXACT_JOB_ID"]' --reason "不再需要此项"
```

每项独立确认accepted/duplicate/failed。失败保留原payload和下一次available，只重试该项；丢失确认后原source root/part身份仍使job不重复。未确认项不按TTL、尝试次数或容量静默删除。Hook补传只证明委托持久化，原生正文仍由唯一JSONL owner采集。原循环每轮有限drain，服务重启后继续；没有额外spool daemon。

## 背压与取消

小时/日额度耗尽时原job保留并延期，reason=model_budget_exhausted，拒绝本身不增加attempt或模型调用。资源/索引失败继续原重试状态，原因、积压、最早等待、下次可用时间可从queue-status读取。failed/verification_failed保留，需要时使用原retry_jobs显式恢复。原calls账本不会因重试/取消清零。

共享资源不可用还会在原meta记录judge或index阶段的退避期限：其他相关job暂缓，防止逐个冲击同一个不可用模型/索引；不依赖内存计时，服务重启后仍遵守。queue-status的shared_backpressure显示原因与until，未完成内容保留。该退避不宣称解决了provider连接或原M冷查询延迟。

排队项按精确ID取消，其他项不受影响。processing取消为协作请求，已进行中的调用不强杀；在原检查点停止后续保存。进入saving阶段后不能承诺撤回，结果会说明too_late。证据、审计与已发生外部效果保持原所有权。

## 半行、文件换代与轮换

游标关联物理identity和逻辑epoch。正常追加继续；同尺寸替换、缩短或已观察重写清空旧会话；历史代游标仍保留。文件重命名后，原watcher交原Store.ingest_rotated处理移动源/目标；目标如jsonl.1也可读旧代末尾，并保存别名接收后续修改。新文件先被发现不会覆盖旧代offset。别名按有界页增量协调，保留积压。

未完成行保留原起点，待换行后再提交。超过8MiB的行以明确oversized_line状态有界跳过，后续有效记录继续，不把坏行正文写进错误。每批最多1000记录/16MiB，事件、知识job、游标和代状态在原事务提交，失败全部回滚。

旧无身份游标标记legacy_identity_unverified，不默认重读全部历史。仅文件元数据无法区分两次观察之间原地截断后又长回更大尺寸与普通追加；未观察/无法绑定的轮换路径保持明确缺口。本阶段不构造hash探针，也不声称任意写入方式都能无损重建。

## 部署

按原control.ps1停止并等待实际listener进程退出，备份原数据库/受影响模块，再部署canonical适配并从原入口启动。阶段验收证据见openspec/changes/resilient-capture-delivery/verification.md；用户原配置、模型、队列和后台所有权沿用既有服务。
