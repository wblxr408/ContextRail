# Spec Delta

## Purpose

让个人本地任务能够把 Bridge 的精确要求和证据用于 Rail 工作集与回读，并始终区分来源权威、导出快照和接收者声明。修订、源丢失与预算不足必须显式可见，避免恢复旧目标或静默丢弃硬要求。

## ADDED Requirements

### Requirement: Preserve one way requirement exports
系统 SHALL 原样保留账本、来源 bytes、ID/revision/quote/scope/supersession/acceptance 与明确快照；Bridge task MUST 匹配绑定任务，导出不得回写 bundle。同身份相同内容重送 SHALL 幂等，异内容 MUST 拒绝。

#### Scenario: Revised requirement
- **WHEN** R002 提升 revision 并导入新 checkpoint
- **THEN** 新 packet 使用修订，旧 Ref 保留可读，旧 snapshot 无法作为当前编译。

### Requirement: Validate source and receiver with Bridge authority
系统 SHALL 复用 Bridge 原校验与逐条 receipt；quote 缺失、未提升 revision 的内容变更、越界路径、task/snapshot/receipt revision 不符 MUST 拒绝。

#### Scenario: Stale receipt
- **WHEN** 接收者对当前要求提交旧 revision 或未覆盖全部 ID
- **THEN** 拒绝覆盖声明，且不将导出等同于已接管。

### Requirement: Pin active hard requirements
系统 SHALL 固定有效目标、约束、always_load 与当前验收，superseded 行 SHALL 仅为历史；完整 packet 超预算 MUST 报告具体固定 ID，不能降级为可选召回。

#### Scenario: Latest personal use correction
- **WHEN** 旧产品目标被自用 demo 目标替代
- **THEN** packet 的目标及固定要求遵从新目标，原文历史仍能回读。

### Requirement: Atomic publication and freshness
系统 SHALL 原子发布证据、派生任务要求、指针与快照；源 bytes 已变化、来源缺失或导出已被替代时 MUST 拒绝作为当前 packet。

#### Scenario: Source changed without reimport
- **WHEN** 原始 R002 已修改但尚未重新导入
- **THEN** 旧导出仍可按引用读取，但当前编译明确要求重新导入。

#### Scenario: Interrupted publication
- **WHEN** 发布中发生存储约束错误
- **THEN** 任务目标、版本、工件与快照全部保持发布前状态。

### Requirement: Exact local task navigation
系统 SHALL 提供任务状态、分页快照目录、原文搜索、精确 continue/explain 入口和可复制恢复参数；未指定或错误快照 MUST 拒绝，不自动选择其他任务或最新版本，不创建新聊天。

#### Scenario: Copy exact resume arguments
- **WHEN** 用户从绑定任务目录复制指定快照的恢复 argv
- **THEN** 调用返回该快照的有界 packet，旧版本明确拒绝，另一任务内容不出现在列表或搜索中。
