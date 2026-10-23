# Spec Delta

## Purpose

用一个本地状态入口解释当前任务、存储中的当前需求、已捕获原生证据与知识队列各自状态。保留来源错误及适配器能力证据，帮助区分捕获成功、尚未学习、只读可用和未控制出站历史等阶段。

## ADDED Requirements

### Requirement: Explain observed task and memory state
状态入口 SHALL 汇总task/version/owner、需求、捕获证据、学习队列、最近来源错误和能力，范围与缺口明确。

#### Scenario: Capture exists without learned fact
- **WHEN** 原生事件存在但知识尚未保存
- **THEN** 分开显示捕获和知识状态，不用任一状态代替另一阶段成功。

### Requirement: Unknown stages remain unknown
没有业务证据的阶段 SHALL 保持未知；状态展示MUST不触发额外模型调用或以静态配置冒充当前存活/历史控制。

#### Scenario: Only reading is supported
- **WHEN** 当前adapter只有可信只读能力
- **THEN** 展示读取支持及出站控制未知，不声称已替换历史。
