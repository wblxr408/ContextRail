# Spec Delta

## Purpose

在长期记忆采集进入既有队列、源事实持久化或缩略展示之前，按明确版本策略处理已知凭据形态和嵌套敏感字段，并保留来源角色与处理状态。精确任务证据和其脱敏派生版本分别定位，避免把改写后的字符偏移伪装成原始证据字节位置。

## ADDED Requirements

### Requirement: Redact capture before persistence
系统 SHALL 在记忆候选入队、持久化和缩略前统一脱敏，覆盖嵌套敏感字段、常见参数/token/认证头/URL用户信息/私钥；错误 MUST 不回显原输入。无法安全处理的格式或边界应拒绝，不存未处理值。

#### Scenario: Nested credential document
- **WHEN** 显式导入的JSON文档及参数含嵌套密码与认证头
- **THEN** jobs及后续缩略中只含处理后数据，来源角色保留，凭据不进入队列或错误文本。

### Requirement: Typed processed values and role provenance
系统 SHALL 明确区分原始值与策略处理后的值，携带版本及来源角色，重复处理不改变结果。MUST 不信任输入字典自报sanitized标记来跳过处理，处理后的引用仅描述该版本数据。

#### Scenario: Retry or forged processed marker
- **WHEN** 已处理值被重送或原输入伪装为已脱敏字典
- **THEN** 合法处理值保持稳定，伪造标记不能绕过敏感字段处理。

### Requirement: Separate precise evidence from redacted projection
可信宿主显式保存的原始任务bytes SHALL 保持其原契约；脱敏展示 MUST 另存为有来源映射的派生版本，派生区间从自身版本计算，不用原文偏移回读改写文本。

#### Scenario: Credential length changes during projection
- **WHEN** 原文一段凭据被替换为长度不同的占位符
- **THEN** 原ref仍读到精确原文，派生ref读到脱敏内容，manifest明确两者来源与偏移边界。

### Requirement: Reuse existing memory ownership
系统 SHALL 将处理接入既有listener、显式导入和retain入口，不增加默认存储或观察者。MUST 保留知识候选规则和事件身份责任，既有历史原件的追溯性不被静默声称已重新脱敏。

#### Scenario: Native queue and retain adapters
- **WHEN** 既有记忆应用接收新文档、监听候选或显式事实
- **THEN** 使用同一版本策略处理后再存入原有队列/源目录，来源状态可核对，未激活的运行进程不被报告为已生效。
