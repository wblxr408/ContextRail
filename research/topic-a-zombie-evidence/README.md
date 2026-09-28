# Topic A — Zombie Evidence: Re-Exposure of Deleted/Invalidated Sources via Derived Context

状态：立项中（2026-09-23）。这是研究选题文件，不是已完成工作。

## 一句话命题

删除/失效一个源，不足以清除它的影响——派生的摘要、embedding、索引项、缓存会继续
重新暴露已被删除的内容。ContextRail 把派生物绑定到不可变源 revision（SHA-256）+
epoch fencing，能 detect-and-refuse 源已失效的派生物。

## 真实 gap 出处（均 live 核实，非训练知识）

| 论文 | arXiv | 明确 stated 的 limitation |
|---|---|---|
| Eywa: Provenance-Grounded Long-Term Memory | 2605.30771 | 自述"尚未报告跨投影的端到端擦除测试（projections, logs, traces, backups）" |
| MemLineage: Lineage-Guided Enforcement | 2605.14421 | "Deletion is deliberately not destructive"；"Prevention is not recovery"；append-only + tombstone，不清派生物 |
| OriginBlame: Token-Level Data Provenance | 2607.13037 | 删除传播只针对训练集，非 RAG 派生上下文，单跳 |
| MemStrata / SmartVector / TierMem | 2606.26511 / 2604.20598 / 2602.17913 | 全选"归档不删除"，从不面对派生物比源活得久 |

裁决：删除向**派生物**（摘要/embedding/索引/缓存）传播是被反复承认的开放问题。
须与 machine-unlearning-for-RAG 显式区分——那个做参数/语料级删除，不做派生物清除。

## 威胁模型

- 源被删除 / 失效 / 撤回（GDPR 擦除、撤销的租户数据、事实 retraction、过期证据）。
- 攻击/失败：陈旧的派生摘要或索引项继续浮现已删源的内容 → 溯源 + 合规 + 完整性三重失败。
- 攻击者变体：投毒一个派生摘要，使其在源被撤销后仍作为"可信上下文"复活。

## ContextRail 为何是可信研究载体

- 派生摘要经 `complete_compaction` 强制引用当前 snapshot 内的精确 span（span 在源外即拒绝）。
- `load_snapshot` 的 epoch fencing + revision 校验 → 源 revision 失效后，绑定它的派生物可被
  detect-and-refuse。
- scope 隔离（tenant/project/branch/task）限定清除传播的边界。

## 最小实验

1. 构造带时变有效性的语料；运行中删除/失效部分源。
2. 对比"僵尸重现率"：朴素摘要 RAG vs. ContextRail revision-绑定派生 store。
3. 报告：删除后派生物仍暴露已删内容的比率、检测延迟、误拒率（合法新鲜快照被误杀）。

## 候选会议（详见 ../VENUE-ASSESSMENT.md）

- 主：安全系统类（USENIX Security / CCS / NDSS）或 DSN（依赖性+安全，含合规/擦除）。
- 不适合：FSE（对称密码，完全无关）、CSF（形式化基础，本题偏经验系统）。

## 诚实边界

引用有效只证来源，不证语义完整。"僵尸重现率"是可执行的机制指标；
是否等价于真实合规违规需另立 oracle。gap 出处最硬（两篇明确 stated limitation）。
