## ADDED Requirements

### Requirement: Workscene facts preserve observed sources
系统 SHALL 从原事件与宿主结果保留文件/范围、cwd、Git和环境线索，区分请求、已观察、失败及未知，引用精确原文；不得以调用请求代替成功。

#### Scenario: A commit was requested without a result
- **WHEN** 来源只有git commit调用或不相干session的结果
- **THEN** 工作现场记录请求和未确认，不能声明提交成功

#### Scenario: Observed result arrives later
- **WHEN** 同范围session/call的结果在后页出现或重新导入
- **THEN** 转换保留实际状态和出处，幂等重送不产生第二个事实效果

### Requirement: Task completion requires bound effect evidence
系统 MUST 保留任务/计划/阻塞/放弃及inferred，完成声明不得自动关闭状态；factual completed需预先绑定的实际输出证据。

#### Scenario: A positive reply follows a blocker
- **WHEN** 助手说已搞定但没有绑定成功结果
- **THEN** 阻塞仍可恢复，回复至多是声明

### Requirement: Action recovery never retries unknown effects
系统 MUST 记录显式执行方式、宿主session/进程身份及部分输出；中断后区分运行、已停结果未知和已确认终态，未知动作不得自动重跑。

#### Scenario: Parent observer closes during execution
- **WHEN** 工作仍运行或PID已经消失但没有exit回执
- **THEN** 恢复提供原句柄/补读入口和准确未知，原动作ID继续阻止执行

#### Scenario: Exact owned process tree is cancelled
- **WHEN** 宿主明确取消且创建时间身份吻合
- **THEN** 处理该进程树，记录确认或未知；不终止其他动作或身份不符进程

### Requirement: Recovery materials are bounded and explicit
系统 SHALL 提供有界只读查询和STATE导出，保留来源、verified/inferred/unknown及运行作业入口，不替换原不可变要求账本。

#### Scenario: A reader has a small budget
- **WHEN** 工作现场超过一页
- **THEN** 返回有效有界JSON、next offset和补读，不把未展示内容声称完整
