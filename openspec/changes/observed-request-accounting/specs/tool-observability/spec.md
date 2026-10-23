# Spec Delta

## Purpose

让日常任务能追到实际工具返回和后续回读的成本，统计跨MCP重连保留，并独立解释原生会话usage与当前任务的关联缺口，不将存储量或小包预览冒称实际模型输入削减。

## ADDED Requirements

### Requirement: Persist tool and readback observations
既有工具边界 SHALL 记录成功/失败、应用响应字节、回读字节及耗时，按工具call/可用请求身份关联，重连可回看；未观测量为null，工具失败不冒称读取成功。

#### Scenario: Small preview followed by large readback
- **WHEN** 大输出只先返回预览，随后多次读取正文
- **THEN** 报告保留两类成本和具体call，不以首次小返回推断整个任务节省

### Requirement: Show scope and partial metrics
只读统计 SHALL 按请求/工具/原生来源展示单位、范围、已知和未知数量及分页入口；无对照的token或费用收益 MUST 保持未知。

#### Scenario: Reader reconnects and one measurement is absent
- **WHEN** 新MCP进程读取已有任务统计
- **THEN** 已保存计量仍存在，缺失字段不表现为零费用或零耗时

### Requirement: Native usage has its own source identity
原有事件owner SHALL 保留Codex token_count的报告usage、精确会话与时间来源，累计与last分开、重复不累加、空值不伪造；不得把会话usage绑定到未观测的Rail请求。

#### Scenario: Repeated cumulative report and null ping
- **WHEN** 同会话重复报告相同累计值或info=null
- **THEN** 累计不翻倍，保留未知状态和原事件引用
