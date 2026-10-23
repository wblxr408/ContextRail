# Proposal

## Why

原生事件已统一归属原监听器，但项目捕获规则、离线逐项交付和延期原因尚未贯通。现有游标仅比较大小，文件替换/轮换时可能漏读或混入旧会话，预算耗尽则没有可见的恢复时间。

## What Changes

- 项目级full/metadata_only/skip策略在原文持久化和知识队列前执行；用户文本独立配置，未知工具不推测文件权限。
- 在原M SQLite内增加持久交付收件与逐项ack，仍交原导入队列；Hook只补无正文委托，不成为第二原生采集者。
- 原jobs延迟、预算和资源错误有可见状态，保留积压、显式取消；恢复由既有循环负责。
- 原游标关联文件身份/epoch和轮换别名，保留半行、显式过长/错误状态，事务失败可从原位置继续。

## Capabilities

### New Capabilities

- `capture-disposition`: 明确项目策略与采集正文边界。
- `capture-delivery`: 持久交付、逐项确认与队列恢复/取消。
- `source-stream-recovery`: 原生源文件身份、半行、轮换和中断续读。

### Modified Capabilities

无已发布主spec；复用原M事件身份、jobs/cursors和资源服务。

## Impact

canonical Python适配与原engineering-memory listener/history_import/deploy、A01配置及使用文档。不增加默认数据库、后台或MCP目录，不继承上游TTL/容量删除。F05–F08作为一阶段验收后提交上传。
