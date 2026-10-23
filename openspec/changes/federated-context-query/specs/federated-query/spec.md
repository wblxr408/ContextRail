# Spec Delta

## Purpose

让一次批量请求在任务证据、明确选择的原生会话与main长期记忆中检索，并按相关性或真实时间浏览。每个来源保留独立失败和覆盖范围，历史材料保持历史角色，不能伪装成当前任务要求或事实。

## ADDED Requirements

### Requirement: Both views include all bound sources
系统 SHALL 对1–8个查询提供relevance/timeline视图，两种视图均查询所有已配置来源，各源有独立条数、字节与耗时边界。

#### Scenario: Memory temporarily unavailable
- **WHEN** 一个来源不可用
- **THEN** 其他来源仍返回结果，并显示具体来源缺口及可重试状态。

### Requirement: Preserve source identity and evidence role
每项 SHALL 标来源、task/session、实际版本或未知原因、真实来源时间状态及可回读ref。历史来源 MUST 不自动提升为当前task事实。

#### Scenario: Undated historical note matches
- **WHEN** 长期记忆没有确切发生时间/版本
- **THEN** 保留未知及日期精度，不以当前时间或阅读会话伪造来源身份。

### Requirement: Source bounds are host configuration
路径、workspace、原生会话和原M连接 SHALL 由可信宿主绑定确定，模型参数MUST不能扩大范围或启动另一份记忆服务。

#### Scenario: Model attempts path override
- **WHEN** 查询携带未支持的源路径覆盖
- **THEN** 在来源调用前拒绝，原绑定范围不变。
