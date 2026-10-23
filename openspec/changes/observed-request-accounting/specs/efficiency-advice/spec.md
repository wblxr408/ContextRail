# Spec Delta

## Purpose

把已有摘要估计扩展为事件触发、单位明确的净收益建议，计入生成、校验、回读与缓存损失和未来使用，帮助自用任务决定保留原证据包或考虑整理，避免固定轮次触发额外模型成本。

## ADDED Requirements

### Requirement: Advice requires comparable costs
收益建议 SHALL 明确unit和测量/估计边界，计算未来节省减生成/校验/预期回读/缓存损失/写入成本；缺项或混用单位 MUST 给insufficient_data，不补造零成本。

#### Scenario: Short task cannot amortize summary generation
- **WHEN** 未来轮数不足或净收益非正
- **THEN** 建议继续当前证据包，并显示成本/轮数与盈亏平衡依据

### Requirement: Event triggered advisory only
摘要/缓存建议 SHALL 复用明确事件触发并保持只读；固定轮数本身不得触发生成，无实际对照不得宣称已节省。

#### Scenario: Turn count without a material event
- **WHEN** 请求只因固定轮数提出整理
- **THEN** 不建议自动生成，已有证据仍可用；建议不会启动模型或后台
