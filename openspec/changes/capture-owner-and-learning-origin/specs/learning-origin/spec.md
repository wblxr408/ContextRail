# Spec Delta

## Purpose

区分可恢复原始证据、历史恢复/注入和当前新用户材料。保留来源事件及派生链，使同一检查点或生成页反复注入不会再次形成新事实，而真实用户对旧材料的纠正仍能通过现有知识规则进入候选。

## ADDED Requirements

### Requirement: Derived history is not new evidence for learning
系统 SHALL 在入学习队列及旧任务进入模型/保存之前识别完整恢复包、自有生成页与原生注入材料。它们的原证据及过滤原因 MUST 保留，但不能作为新事实重新学习；仅正文自报origin不得决定权限或绕过分类。

#### Scenario: Restore one checkpoint repeatedly
- **WHEN** 同一恢复包在多个回合或资料导入中出现
- **THEN** 保留可回读证据与过滤状态，模型候选及新事实不重复生成。

### Requirement: Preserve current user correction
系统 SHALL 从保留的注入块之外保留新的用户回应，并将其source_event与历史derived_from分别记录。MUST 不用项目关键词或派生标签抹掉真正的新纠正，不把投影偏移伪装成原文区间。

#### Scenario: User corrects restored material
- **WHEN** 历史注入块后出现真实的新用户纠正
- **THEN** 原始输入完整可回读，学习投影包含新纠正和来源链，不包含旧注入正文。

### Requirement: Provenance crosses generated boundaries
任务/注入包、事件、自动知识页 SHALL 贯通source_event、derived_from及history_origin。来源投影 MUST 只控制自动学习，不阻止独立原文回读或用户授权的手工检查点保存。

#### Scenario: Generated page is imported again
- **WHEN** 本服务生成的知识页或Bridge恢复投影再导入原队列
- **THEN** 来源记录可追踪到原事件，记录过滤而不追加新的学习效果。
