# Spec Delta

## Purpose

让同一项目中的多个任务和原生会话各自恢复其明确版本与当前要求；候选发现和内容预览不会消费待恢复点，只有显式领取并核对送达后才转交原任务租约。

## ADDED Requirements

### Requirement: Exact identity and candidates
系统 SHALL 按task/version/checkpoint/session精确绑定恢复；缺少或匹配不到身份时返回可区分候选和缺口。MUST NOT 选择同项目最新快照作为替代。

#### Scenario: Two tasks in one project
- **WHEN** 两个任务各有快照，调用者在任务A中指定任务B的ID
- **THEN** 不返回B的正文、不读取项目最新作为A，给出A的候选和身份缺口

### Requirement: Read-only discovery
候选展示和preview SHALL 保持原owner/epoch/待交接点不变，并显示要求版本、意图覆盖和来源新鲜度。

#### Scenario: Browse pending handoff
- **WHEN** 用户查看另一个候选或重复预览待交接快照
- **THEN** 原准备状态保留，没有新增领取或激活效果

### Requirement: Explicit idempotent acceptance
只有可信宿主指定精确offer、目标会话、版本和逐项回执后系统 SHALL 推进原handoff协议。相同操作重送返回同一交接结果；不同参数复用ID、来源变化、错接收端和未送达必须拒绝。

#### Scenario: Lost response after transfer
- **WHEN** 已激活后同一接收端重送完全相同领取
- **THEN** 返回原lease且epoch只增加一次，其他候选不受影响

#### Scenario: Source changed before acceptance
- **WHEN** offer准备后外部Bridge来源已改或任务版本不符
- **THEN** 拒绝接管并保留原协议状态，明确要求重协调，不能静默换一个检查点
