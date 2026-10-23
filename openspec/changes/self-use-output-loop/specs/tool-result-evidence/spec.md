# Spec Delta

## Purpose

将宿主已经执行的工具结果完整保存为任务证据，并以有界、可解析的结果供模型读取。消费者能凭精确引用恢复 stdout、stderr 与执行状态，而不会因缩略把失败误报为成功。

## ADDED Requirements

### Requirement: Persist complete observed result

系统 SHALL 原子保存结果状态、stdout 与 stderr；同 call ID 同内容重送 SHALL 返回已有记录，异内容 MUST 拒绝，不能自动重执行动作。

#### Scenario: Large multilingual log
- **WHEN** 宿主提交包含中文、CRLF、emoji 和末尾异常的大日志
- **THEN** 小结果含精确引用，重新打开库后可读取末尾异常与完整字节。

#### Scenario: Retry conflict
- **WHEN** 已有 call ID 以不同内容再次提交
- **THEN** 原记录和输出保持原状，并返回冲突。

### Requirement: Bounded valid presentation

系统 SHALL 保持展示 JSON 可解析且 UTF-8 字节数不超过明确预算；完整原文 MUST 不受展示缩略影响，必需元数据超限 SHALL 明确拒绝。

#### Scenario: Tight display budget
- **WHEN** 中文结果大于展示预算
- **THEN** 显示缩略状态和回读引用，JSON 可解析，stdout/stderr 状态仍完整。

### Requirement: Tool aware outcome classification

系统 SHALL 保存实际退出码并区分成功、失败、无匹配、超时、部分结果和未知；任意非零失败不得仅因有 stdout 而软化。

#### Scenario: Output followed by failure
- **WHEN** 构建命令先打印普通文本再以 1 退出
- **THEN** 状态为失败，退出码和 stderr 仍可回读。

#### Scenario: Search no match
- **WHEN** 明确 rg/grep 搜索以 1 结束且 stdout/stderr 均空
- **THEN** 状态可为 no_match，其他形态仍按失败处理。
