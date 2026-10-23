# Design

## Context

Store连接绑定创建线程，默认构造会以BEGIN IMMEDIATE初始化/升级；不能在线程池里直接复用宿主连接或构造可能写入的Store。ContextTools已有固定scope/session和只读get/extract，B03提取返回source跨度。借鉴context-mode runPool的按输入索引保留settled结果，改为本地只读证据批处理。

## Goals / Non-Goals

独立证据读取/提取的有限并发、逐项失败隔离与有界结果。依赖任务、写入、授权和动作账本仍由原宿主顺序执行，不进入批处理。各项独立read transaction，不承诺整个批次看到同一原子时间点。

## Decisions

1. 新增Store.open_readonly：mode=ro/query_only连接，校验现有当前schema，不初始化/迁移。工作线程各自打开/关闭，传同一clock以保留expiry语义。宿主通过database_list取得连接真实路径，不从模型提供路径。
2. context.batch参数calls/concurrency/per_call_bytes/budget；最多32项，唯一id，每项只有id/tool/arguments。固定allowlist get/extract。嵌套/依赖/写动作直接整批拒绝；合法工具的参数错误是单项失败，不影响其他调用。
3. concurrency默认2、最多8；内存库只允许显式1，宿主已有活动transaction拒绝，避免未提交视图与嵌套事务歧义。线程池限制在工作数以内，返回按原序遍历futures，错误保留稳定code。
4. 先保留所有原请求、身份、状态和错误码容量，预算不足在调度前拒绝。每项响应也受per_call_bytes限制，默认2048。整体过大时先移除可选错误message，再整体省略最大的result并delivery=omitted；不截断结果JSON。完整读请求可直接单项重试。
5. CLI batch-read接收JSON数组文件或stdin，复用同一ContextTools与MCP注册。没有第二队列或新daemon。

## Risks / Trade-offs

提取主要CPU计算 → Python线程不承诺CPU加速，仅限制I/O在途数量。每项read snapshot独立 → 输出consistency明确，不包装成原子批次。只读文件在打开前删除/替换 → mode=ro失败或schema拒绝，不创建空任务库。必要原请求也可能很大 → 单项参数及总输入大小有界，展示预算不足明确拒绝。

## Evidence

https://github.com/mksglu/context-mode/blob/a92cf6a77ac02971fb5fafe2155e5ee229491884/src/runPool.ts
