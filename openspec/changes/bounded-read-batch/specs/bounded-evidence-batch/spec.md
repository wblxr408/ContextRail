# Spec Delta

## Purpose

让宿主与模型一次提交多个已经保存证据的独立读取或提取任务，按输入身份和顺序取得每项结果。并发、失败隔离与整体预算明确，单项出错或结果省略都不丢失其他调用的状态和继续读取依据。

## ADDED Requirements

### Requirement: Ordered independent read batch
系统 SHALL 接受最多32个带唯一ID的独立 context.get/context.extract 调用，按输入顺序返回逐项状态、原请求与结果或错误。MUST 拒绝写入、授权动作、嵌套批处理和依赖声明，不调度外部执行。

#### Scenario: One missing artifact
- **WHEN** 中间项读取不存在的revision，其余两项读取或提取成功
- **THEN** 中间项保留not_found，其余结果照常返回，身份与顺序不变，任务版本不变。

### Requirement: Bounded concurrent read views
系统 SHALL 将请求并发限制在1至8，实际并发不超过调用数。并发读取 MUST 使用同一现存数据库的独立只读连接，不初始化或升级数据库，不复用跨线程SQLite连接。

#### Scenario: In memory store
- **WHEN** 数据库没有可重开的文件路径
- **THEN** 仅允许concurrency=1的顺序读取，不复制新数据库或默默改变一致性。

### Requirement: Complete bounded response
系统 SHALL 按完整UTF-8 JSON核算批量预算，保留所有调用身份、状态及继续读取的原请求。可省略完整结果并明确标记，MUST 不截断协议；预算无法容纳必需元数据时在调度前拒绝。

#### Scenario: Oversized successful result
- **WHEN** 多个成功结果超过整体展示预算
- **THEN** 省略部分完整结果并保留成功状态与可重试原请求，失败项错误码仍可见。
