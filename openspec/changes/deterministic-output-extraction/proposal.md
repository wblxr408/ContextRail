# Proposal

## Why

大输出已完整落证据，但用户仍要手动翻日志、JSON、CSV 和 Git 输出。实现 B03，把确定性统计和筛选接到已有证据，同时保留精确回读定位及解析失败状态。

## What Changes

- 新增只读证据提取器：日志错误聚合、JSON Pointer/记录筛选、CSV 数值统计、Git porcelain-v1 -z/numstat -z 字段。
- 每项结果携带原 revision、字节跨度、行或记录定位；报告扫描范围、过滤条件、结果省略与未完成解析。
- 接到绑定 ContextTools/MCP 和显式 CLI；不执行命令或任意表达式，不写回原文。

## Capabilities

### New Capabilities
- `evidence-extraction`: 已保存输出的确定性字段、统计和精确来源定位。

### Modified Capabilities
无。

## Impact

沿用 Python 标准库、SQLite Store、ContextTools 和既有 MCP adapter。没有新依赖或外部服务，原始 stdout/stderr 与引用继续由 B02 保存。
