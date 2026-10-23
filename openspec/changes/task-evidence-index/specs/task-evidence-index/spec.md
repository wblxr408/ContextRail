# Spec Delta

## Purpose

为已经保存的任务证据提供可重建的全文候选索引，按精确来源版本及字节跨度返回可核对材料。索引覆盖状态、失效状态和查询预算必须明确，避免混入其他任务或把未索引范围解释为没有匹配。

## ADDED Requirements

### Requirement: Task scoped indexed candidates
系统 SHALL 提供FTS5/BM25候选检索，结果仅来自绑定任务的可读当前工件；每项 MUST 携带name/revision/start/end及匹配依据，原文字节可回读。

#### Scenario: Same artifact names in different tasks
- **WHEN** 多个任务存在同名工件或相同查询词
- **THEN** 结果只含绑定任务，候选定位精确，其他任务内容不进入返回。

### Requirement: Atomic index lifecycle
系统 SHALL 使新证据与索引状态一致提交，版本替换、失效和过期后不返回不可读候选。无法索引的内容 MUST 声明pending/excluded及原因，不以空索引冒充全量无匹配。

#### Scenario: Failed second write
- **WHEN** 同一批第二件证据的索引提交失败
- **THEN** 整批工件、任务版本及派生索引同时回滚，不能只保存一半。

### Requirement: Rebuild without changing task authority
系统 SHALL 支持按明确任务、owner租约重建派生索引；旧库升级 MUST 保留工件、需求、会话和快照，未回填内容标pending。重建不得取得新写权或推进任务事实版本。

#### Scenario: Upgrade existing task database
- **WHEN** 已有任务库升级并显式重建该任务索引
- **THEN** 原任务与快照仍可按原标识读取，重建后候选可用，其他任务索引不被重置。

### Requirement: Bounded query and explicit coverage
系统 SHALL 限制查询输入及完整JSON输出预算，声明覆盖、排序含义和结果省略。FTS不可用 MUST 可见，原文读取和既有字面搜索继续可用。

#### Scenario: Pending evidence and narrow budget
- **WHEN** 只有部分文本已索引或展示预算不足
- **THEN** 返回partial/index_pending或明确省略，不把未扫描材料断言为无异常；硬元数据装不下则budget_exceeded。
