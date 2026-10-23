# Spec Delta

## Purpose

让恢复接收者区分已选正文、仅短述、目录未展示、不支持导入以及未观测历史，逐来源携带单位、原因和恢复入口，避免把索引或队列保存误认为完整恢复。

## ADDED Requirements

### Requirement: Loss remains explicit by source
简报、Bridge投影、原生注入及显式导入结果 SHALL 提供逐来源total/selected/omitted/truncated/unsupported/unknown，明确单位、范围、计数关系及补读方式；未知 MUST 使用null或明确状态。

#### Scenario: Main hook independently delivers unknown history
- **WHEN** Rail无法观察独立main Hook的正文
- **THEN** memory覆盖保持unknown，不能报告零遗漏或完整历史

#### Scenario: Import page contains unsupported files
- **WHEN** 已保存目录含不支持的文件与未处理页
- **THEN** 导入结果分别呈现不支持项、分页缺口和已排队项，不能把queued当saved

### Requirement: Portable state preserves gaps
主机显式导出的STATE SHALL 携带精确快照、来源缺口、要求和回读入口；已有目标文件 MUST 拒绝覆盖。

#### Scenario: State exported twice to same path
- **WHEN** 目标STATE已存在
- **THEN** 拒绝覆盖且保留原内容；首次导出包含可恢复缺口
