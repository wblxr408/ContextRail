# Spec Delta

## Purpose

提供无需模型调用的日常恢复解释，使用户能从当前精确简报追到必需要求、被选材料、留冷原因和实际处理步骤，并连接已有任务时间线与动作状态。

## ADDED Requirements

### Requirement: Explain exact generated view
只读简报/解释入口 SHALL 展示必需/请求/优先级/分组加载理由、来源/版本、未覆盖要求和留冷ref，并连接E08时间线和真实动作状态；解释 MUST 不冒充模型消费证明。

#### Scenario: Budget leaves optional material cold
- **WHEN** 用户生成或解释某精确快照
- **THEN** 显示本次预算下各层、留冷原因和补读工具，附分页时间线/动作与最近准备回执

#### Scenario: Stale Bridge authority
- **WHEN** Bridge源在导出后被修改
- **THEN** 入口拒绝当前恢复，不自动换其他快照或把旧要求标为当前

### Requirement: Explanation is bounded
解释响应 SHALL 受完整UTF8字节预算约束，截短动作或时间线展示时保留总数与继续入口，不依赖额外模型或main召回。

#### Scenario: Many task actions and timeline entries
- **WHEN** 附加经历超过可用空间
- **THEN** 省略展示有计数和页游标，必需证据不被静默删减
