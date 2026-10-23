# Spec Delta

## Purpose

让宿主把最终序列化请求与当前证据包关联，按明确单位记录真实输入、工具schema、系统与历史及预留，保持缺失usage为未知，避免以证据包大小冒充整个模型请求。

## ADDED Requirements

### Requirement: Account observed final requests
宿主入口 SHALL 记录实际最终payload字节及可验证的分项范围，token仅来自显式计量器或报告usage；未提供数据 MUST 保持null并说明来源，不存原始提示正文。

#### Scenario: Same packet in different host envelopes
- **WHEN** 同一packet被两个不同大小的最终请求使用
- **THEN** 最终请求量分别记录，packet大小只作为关联证据，分项不能越界或重叠

### Requirement: Durable idempotent request attribution
请求观测 SHALL 复用原UsageLedger，绑定scope/session/snapshot和稳定观测ID；同计量/归属回执重送只有一个效果，异计量或归属复用ID MUST 拒绝。正文不保留，等长payload内容一致性不得声称已验证。

#### Scenario: Transport retry after original receipt
- **WHEN** 同一宿主观测重送
- **THEN** 返回原记录，统计不翻倍；真实不同请求/attempt可独立记录

### Requirement: Respect usage subset semantics
OpenAI报告口径 SHALL 将cached视为input子集、reasoning视为output子集；缺值不填0，不能相加重复计费。

#### Scenario: Usage contains reasoning tokens
- **WHEN** usage报告output和reasoning
- **THEN** 原output保持不变，reasoning单独保留作为子项
