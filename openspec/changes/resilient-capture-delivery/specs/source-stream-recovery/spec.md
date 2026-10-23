# Spec Delta

## Purpose

使原生JSONL采集在半写、文件替换、常见重命名轮换和事务中断后继续处理同一来源代，避免复用旧会话状态，并显式报告无法依据文件元数据确定的覆盖范围。

## ADDED Requirements

### Requirement: Source generation and partial records
系统 SHALL 关联文件身份、逻辑epoch和原游标；已观察的替换/缩短必须开始新代并清空会话。未完成行保留可继续位置，过长或坏行必须有明确状态。

#### Scenario: Same-size replacement
- **WHEN** 原路径换成同尺寸的新文件
- **THEN** 读取新文件的会话元信息，不从旧offset跳过或串用旧上下文

### Requirement: Rotated tail recovery
系统 SHALL 在原监听器收到重命名时保存实际路径别名并补读旧代末尾，即使目标扩展名变化；先发现新文件也不得覆盖旧代游标。

#### Scenario: Rotation before final read
- **WHEN** 旧代末行完成后移为jsonl.1，新文件先被采集
- **THEN** 原移动通知仍能用历史代读回旧末行，新会话独立，重送不重复入队

### Requirement: Transactional continuation and limits
系统 SHALL 原子提交本批事件、job与游标；异常回滚后可从同位置继续。未观察的原地截断再增长等不可区分情况必须标注限制，旧无身份游标不得被宣称完全已验证。

#### Scenario: Failure before cursor commit
- **WHEN** 事务在已解析记录后异常中断
- **THEN** 本批持久化回滚，重试保留来源代并只产生一次业务效果
