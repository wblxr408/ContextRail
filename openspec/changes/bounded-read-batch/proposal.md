# Proposal

## Why

逐项读取与提取已有证据需要多次往返，某一项失败时宿主还要自行保留其他结果。B06 增加有界独立批处理，保持输入身份、顺序和各项状态可追踪。

## What Changes

- 新增 context.batch 与 batch-read CLI，仅接受独立 context.get/context.extract 调用。
- 并发上限可配置，每线程使用同一任务库的只读连接；每项独立成功/失败，按输入顺序返回。
- 整体 JSON 字节有界，必要调用身份/状态/原请求保留，超预算结果整体省略并可按原请求继续读取。

## Capabilities

### New Capabilities
- `bounded-evidence-batch`: 绑定任务的独立只读调用批处理、单项失败隔离与有界交付。

### Modified Capabilities
无。

## Impact

复用 Store/ContextTools、标准库线程池与既有 MCP；不调度写入、授权、依赖任务或外部执行，不新增服务。
