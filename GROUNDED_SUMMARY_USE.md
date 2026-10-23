# 摘要证据与知识页版本（D03 / D04 / D06）

现有 R compaction 与 M reflect/page 已接入字面证据和来源更新。仍使用原 SQLite、vault、资源服务及模型；无新增默认观察者。

## R：摘要提交与回读

`CompactionLifecycle.complete` 和直接 `Store.complete_compaction` 都执行提交门。保留 snapshot constraints/acceptance、必需 Bridge active statement 及显式 resume-intent 当前要求。所引精确 span 中的版本、数值单位和否定必须保留，不接受另一个版本/数值；路径、命令、URL、错误码可只保留在 residue，无需全部写进摘要。

```python
summary = lifecycle.complete(scope, lease, prepared.id,
    title="进展", text=draft, spans=(source_ref,))
material = store.summary_evidence(scope, summary.id)
attempts = store.compaction_attempts(scope, prepared.id)
```

拒绝后 prepared 保持可重试，`attempts` 保存草稿、原 spans 与原因；宿主 validator 拒绝也留回执。scope/lease/current/span 权限校验先执行；不保存越权材料。`context.summary_search` 会检索 residue，`context.summary_get` 附带证据入口，`context.summary_evidence` 按 offset/limit/budget 分页读取；原文继续 `context.get` 精确读取。历史 summary 会标 snapshot_current=false，不应被当成当前结论。

residue 条目包含 kind/text/source/start/end/context/context_start/context_end，范围为原所选文本的 UTF-8 bytes。Windows 路径和中文标点已适配；每来源最多扫描262144 bytes，最多256条，未扫描/省略计入 loss。当前 gate 不接受覆盖未知的草稿，宿主应缩小精确来源窗口，保留必需事实。

## M：合成与页面

`memory_reflect` 在既有引用和 exact support 校验之外，向原模型提供完整所选来源的有界 residue（包括1200字符摘录外的细节），并检查回答中的版本/数值、所引原文的否定与当前权威用户 observation。这些片段来自脱敏后的 note body；byte span 对应 processed body，不是带 frontmatter 的文件原始偏移。

返回 `validation` 明确确定性规则覆盖；可选 claims 分为 source/inference/unknown，source 需真实 citation、exact quote 且 text 包含于 quote。推断/未知不允许携带伪造证明字段；规则不证明任意自然语言蕴含，也不把推断升级为事实。拒绝合成保留在原库 memory_derivation_attempts。

原 `memory_knowledge_page` 增加两种显式读取：

```json
{"name":"页面名","query":"主题","history":true}
```

```json
{"name":"页面名","query":"主题","version":"history 返回的精确版本"}
```

刷新仍用 `refresh:true`。版本先写入不可变文件，再通过原 SQLite 切当前指针，原页面路径是可修复的缓存投影。新来源/修正/删除、生成中变更或刷新失败后，当前响应 `stale:true, content:null`；`previous_version` 与 history 可读旧版和失败草稿。旧六列缓存首次访问保守迁为 legacy_unverified 历史，需显式刷新建立来源绑定。实际版本文件位于原 vault engineering/knowledge-pages/<稳定ID>/versions；未添加额外文件夹订阅。

当前 freshness 使用全库文件元信息 generation，保守包含新增潜在冲突来源；引用集合另存 source_bindings。只在原调用时核对，不增加后台扫描器，不保证观察间不可见的同元信息改写。外部直接读历史文件仍可能读到旧文本；当前性由 page 工具确认。原 notes/recall 继续排除派生页，避免自我学习。

文件与 SQLite 无法跨介质原子：孤立版本文件不成为当前；DB 指针是权威，工具再次读取时修复投影。人工占用的原页面路径拒绝覆盖。在途来源变化没有发布成功的保证，应回读状态后明确刷新。

## 本机更新

通过原 `app/control.ps1 stop` 等待原 lifecycle 退出，备份数据库及本阶段文件，最小复制 memory_runtime/memory_integration、fidelity/derived_pages、reflect schema/instructions，再用原 `start`。现有 MCP 长连接须用其正常重连/新会话加载新版；不需要重新信任未变化的 Hook。

本阶段证据见 openspec/changes/grounded-summary-refresh/verification.md。规则和假生成器消费者不等价于真实模型质量验证。
