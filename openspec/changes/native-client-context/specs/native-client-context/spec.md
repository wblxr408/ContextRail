# Spec Delta

## Purpose

让同一本地任务的 Codex 和 Claude 能各自通过其原生配置读取精确任务包与冷页，并在实际支持的 Hook 阶段组合当前任务和已有长期记忆。绑定、启停、来源、预算和能力缺口必须明确，避免混任务、丢硬要求或虚报客户端接线成功。

## ADDED Requirements

### Requirement: Bound read only packet access
系统 SHALL 注册受 owner 租约约束的 reader，并通过 MCP 提供精确快照任务包；读取参数 MUST 不得重绑 scope/session 或执行命令，reader 不得取得 owner 写权。

#### Scenario: Two client readers
- **WHEN** Codex 与 Claude 的 reader 分别读取同一任务的指定 snapshot 和冷页
- **THEN** 各自得到原任务原文，owner/epoch 不变，非法覆盖与写入拒绝。

### Requirement: Native hook composition preserves hard requirements
系统 SHALL 适配两端 UserPromptSubmit 字段，使用既有记忆 Hook 一次组合原历史和完整任务要求；历史可缩略并标记，硬要求或有效源不可用 MUST 明确拒绝。

#### Scenario: Small history budget
- **WHEN** 可选历史超过整体输出字节预算
- **THEN** 完整 JSON 保持有效，任务原文不缩略，历史标记截断并保留读取边界。

#### Scenario: Claude inline context limit
- **WHEN** Claude 组合上下文将超过原生 10000 UTF-16 单元上限
- **THEN** 缩略可选历史使完整上下文保持 inline；完整硬要求本身超限则明确阻止，不能让客户端只交付前 2000 字文件预览。

### Requirement: Refresh explicit runtime view without changing source
系统 SHALL 仅从明确 base snapshot 和当前有效同一 Bridge 导出刷新当前 task version 视图；owner 租约/freeze 与来源 MUST 校验，重复刷新 SHALL 复用视图，不改变写权。

#### Scenario: Tool evidence advanced task version
- **WHEN** 相同源仍有效但工具保存使 task version 前进
- **THEN** 受授权 Hook 可生成带 base 关联的当前视图，读取端获取新视图，旧源或其他 task 不被自动替代。

### Requirement: Single source install and independent disable
系统 SHALL 生成两端可审阅配置、单一源码入口和明确绑定，不复制新记忆后台；停用 Rail MUST 仍透传既有记忆能力。

#### Scenario: Disable Rail injection
- **WHEN** 绑定的 enabled=false
- **THEN** 原记忆 Hook 输入输出保持原行为，任务证据不注入，长期记忆与 Bridge 独立可用。

### Requirement: Separate client activation evidence
系统 SHALL 分别记录两端任务包/冷页的真实客户端调用与支持阶段，原生信任或渠道失败 MUST 可见，不能仅凭文档、配置或 SDK 客户端名称宣称部署成功。

#### Scenario: Codex hook requires native review
- **WHEN** 新 Hook 尚未经过原生信任审阅
- **THEN** 标记待激活，不写信任状态或绕过审阅，也不推断另一端已通过。
