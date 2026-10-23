## ADDED Requirements

### Requirement: Literal evidence remains source-bound
系统 SHALL 从许可原文保留有界字面细节、UTF-8 来源范围和否定上下文，并向摘要输入及回读提供独立通道与覆盖说明。

#### Scenario: Summary omits a diagnostic
- **WHEN** 中文来源有 Windows 路径、命令、URL、错误码和版本而摘要省略错误码
- **THEN** 用户仍能按该错误码找到摘要及原精确来源，未扫描范围明确标注

### Requirement: Invalid drafts never become current summaries
系统 MUST 在原提交入口核对强制要求、字面事实覆盖和引用；source/inference/unknown SHALL 可区分，拒绝尝试保留草稿、出处和原因。

#### Scenario: Latest restriction or version is wrong
- **WHEN** 草稿遗漏当前限制或把所引版本/数值改成另一个值或丢掉其否定
- **THEN** 原提交拒绝，未新增有效摘要，草稿和拒绝报告可读取

#### Scenario: Exact source claim is accepted
- **WHEN** 草稿保留必需限制与关键字面事实，引用范围和 source 证据真实，推断另行标注
- **THEN** 原 compaction/reflect 接口可提交并返回有界验证报告

### Requirement: Derived versions follow source changes
系统 MUST 绑定生成所用来源版本并在发布前复核，先保存新版本再切当前；失败保留旧版本但标记 stale，stale SHALL 不作为当前正文注入。

#### Scenario: Source changes or refresh fails
- **WHEN** 来源修正、删除或在生成中变化，或者新生成失败
- **THEN** 当前工具响应没有旧版当前正文，历史和失败原因可读；重开后仍可明确刷新

#### Scenario: Successful refresh preserves history
- **WHEN** 生成校验通过且来源版本未变化
- **THEN** 新版成为当前，旧版可按精确版本读取，文件中途失败不会切换当前指针
