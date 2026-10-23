# Spec Delta

## Purpose

在共享记忆服务暂时不可用、批次中断或资源预算耗尽时，保留可恢复的逐项交付与学习工作；确认、延期和取消都有明确状态，重复提交不会重复形成已确认的队列效果。

## ADDED Requirements

### Requirement: Durable per-item delivery
系统 SHALL 先持久化稳定交付身份，再逐项交原队列并返回accepted/duplicate/failed。原生Hook只提交无正文委托。ID异内容必须拒绝，失败仅重试相应项，已确认项不重复学习。

#### Scenario: Interrupted batch
- **WHEN** 批次前项已提交，后一项失败或确认前进程中断
- **THEN** 重启从逐项状态继续，前项的job身份保持唯一，失败项保留并可重试

### Requirement: Backpressure retains work
系统 SHALL 显示积压、最早等待、延期原因与下次可用时间；预算/资源不可用时保留任务并延迟重试，不自动删除未评估资料。

#### Scenario: Exhausted model quota
- **WHEN** 小时或日额度耗尽
- **THEN** 原job保留、available后移、原因可见，预算拒绝本身不增加模型attempt或调用

### Requirement: Explicit cancellation
系统 SHALL 支持按精确job ID显式取消并保留审计；进行中的工作必须说明合作取消边界，不能声称已撤销外部效果。

#### Scenario: Cancel selected queued work
- **WHEN** 用户取消一个排队job
- **THEN** 该项不再调度，其他积压保留，原始证据及已计调用不删除
