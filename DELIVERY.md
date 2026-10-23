# 自用模块交付

按 TODO 持续实施；完成一大阶段后集中验收，再提交上传该阶段。权威清单为 [docs/self-use-integration-todo.md](docs/self-use-integration-todo.md)。当前完成 35/72，其余继续按依赖推进。

## 首次集中上传：截至 D07 / H03 / D08

首次上传包含此前尚未提交的自用功能依赖：项目配置和客户端适配、结果保存与原文回读、Bridge 要求导出、提取与批量读取、索引与目录同步、来源身份与脱敏、原生事件与时间线、三源查询、分层恢复和请求计量。各阶段的契约、实现与验收见 `openspec/changes/`；固定上游及许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

最近阶段 D07 / H03 / D08：12 项当前消费者整阶段通过；并发回执、摘要和缓存生命周期接线另 3 项经具体问题修复后定点通过。最终 wheel 构建及严格 OpenSpec 校验通过；没有为上传重复运行旧套件。实际原生 usage、持久工具统计、精确回读及 R schema10 迁移范围见 [阶段验收](openspec/changes/observed-request-accounting/verification.md)。

源码和指南可审阅；本机运行库、会话日志、配置中的机器绑定、备份及上游研究 clone 保留在本机，按指南在各自环境生成。既有工程记忆 app 的适配源码在 `integrations/engineering_memory/`，仍依赖使用者的原服务，不包含模型与运行数据。

## 下一阶段

E05 / E06 / I02：恢复时保留最新有效要求与末尾纠正；精确绑定任务、要求版本、检查点和会话；候选预览与接管分开，接管幂等且拒绝已变化的来源。整阶段完成后再次提交上传。
