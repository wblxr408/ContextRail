# Topic D — Enforced Boundary-Metadata Survival: Constraint Integrity Across Context Compaction

状态：立项中（2026-09-23）。研究选题文件，不是已完成工作。

## 一句话命题

上下文压缩/摘要时，治理与安全约束（"事实只能给谁用"、"重试前必须先查交易状态"）
被优先丢弃，而操作性事实被保留——"事实存活，规则消失"。已有工作只**测量**这个崩塌，
或提出被 in-context 假冒击败的软防御。ContextRail 用 span-anchored + 依赖闭包成组 +
不可变带外 store，把约束存活变成**构造上的强制**，而非尽力而为。

## 真实 gap 出处（均 live 核实）

| 论文 | arXiv | 明确 stated 的 limitation / hand-off |
|---|---|---|
| Boundary Metadata Collapse in Multi-Agent LLM Handoffs | 2608.29028 | 开放问题="自动类型化边界抽取与验证"；其最强防御仅"可审计策略面，非无错保证"——**只测量不强制** |
| Governance Decay: Context Compaction Erases Safety Constraints | 2606.22528 | 提 Constraint Pinning，但自述"被 in-context 操作者假冒击败，需要一个可信带外操作者通道"——**而它没建那个通道** |
| CompInt: Side-Constraint Loss under Compaction | 2608.11242 | 当前压缩器"平均只保留 17% 注入的 Session Constraint"；SC 感知提示保留率仍 <40% |
| AI Guardrail Survival under Self-Summarization | 2608.11392 | "presence check is not a safety check"——残缺规则比完整规则更常触发禁动作；不提防御 |

裁决：约束存活经压缩=被反复承认的开放问题。Governance Decay **直接点名缺一个"可信带外
通道"**——ContextRail 的不可变、epoch-fenced、scope 隔离证据 store 正好是。

## 威胁模型

- 诚实但有损的摘要器（压缩下丢规则）+ in-context 操作者假冒攻击（在近轮 token 流里断言假权威）。
- 攻击者伪造不出解析进签名快照的 span——这是防御的密码学锚点。

## ContextRail 为何是可信研究载体

- 每个摘要 span 必须引用当前 snapshot 内的精确源 span（`complete_compaction` 的 `_validate_spans`，
  span 在源外即拒绝）→ 边界元数据成为不可丢的类型化关系。
- 边界元数据放进依赖闭包原子组 → 事实不能存活而其治理规则被丢（成组 load-whole-or-nothing）。
- 不可变 store = Governance Decay 说缺的"可信带外通道"；span-membership 对 in-context 假冒可篡改检测。

## 最小实验

1. 复现 BOUND-Handoff / ConstraintRot episodes（跨厂商 DeepSeek+Kimi）。
2. 三臂对比：(a) 基线压缩 (b) Constraint Pinning 重注入 (c) ContextRail span-anchored 成组压缩。
3. 加 in-context 操作者假冒注入；指标=违规率 + "存活规则是否仍触发"（取 2608.11392）。
4. 假设：(c) 违规率近 0 且抗假冒，(b) 在假冒下退化。

## 候选会议

- 主：DSN（依赖性+安全，治理约束存活正对口）或安全系统类。
- 诚实：Governance Decay 的 Constraint Pinning 是最强现有防御——novelty 精确在
  "构造式强制 + 密码学 span-membership 抗 in-context 假冒 + 原子性使规则-事实同进退"，
  **不要 claim 首次发现压缩丢约束**（那是 2606.22528/2608.29028 的结果）。

## 诚实边界

违规率、规则触发率可执行。gap 出处硬（多篇 stated limitation，且一篇点名缺你有的东西）。
须显式区分"我强制存活"vs 它们"测量崩塌 / 软重注入"。
