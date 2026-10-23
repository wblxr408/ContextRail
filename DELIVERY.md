# 自用模块交付

按 TODO 持续实施；完成一大阶段后集中验收，再提交上传该阶段。权威清单为 [docs/self-use-integration-todo.md](docs/self-use-integration-todo.md)。当前完成 42/72，其余继续按依赖推进。

## 首次集中上传：截至 D07 / H03 / D08

首次上传包含此前尚未提交的自用功能依赖：项目配置和客户端适配、结果保存与原文回读、Bridge 要求导出、提取与批量读取、索引与目录同步、来源身份与脱敏、原生事件与时间线、三源查询、分层恢复和请求计量。各阶段的契约、实现与验收见 `openspec/changes/`；固定上游及许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

最近阶段 D07 / H03 / D08：12 项当前消费者整阶段通过；并发回执、摘要和缓存生命周期接线另 3 项经具体问题修复后定点通过。最终 wheel 构建及严格 OpenSpec 校验通过；没有为上传重复运行旧套件。实际原生 usage、持久工具统计、精确回读及 R schema10 迁移范围见 [阶段验收](openspec/changes/observed-request-accounting/verification.md)。

源码和指南可审阅；本机运行库、会话日志、配置中的机器绑定、备份及上游研究 clone 保留在本机，按指南在各自环境生成。既有工程记忆 app 的适配源码在 `integrations/engineering_memory/`，仍依赖使用者的原服务，不包含模型与运行数据。

## 第二模块：E05 / E06 / I02

恢复时保留最新有效要求与末尾纠正；精确绑定任务、要求版本、检查点和会话；候选预览与接管分开，接管幂等且拒绝已变化的来源。原生内联限制下保留必需材料，来源清单与冷页仍可精确回读。对应消费者、实际应用和已知覆盖见 [本阶段验收](openspec/changes/precise-resume-intent/verification.md)。

已上传模块：[715dfdbf](https://github.com/wblxr408/ContextRail/commit/715dfdbfa0ff038b44c8181fdfb508b6efb2b158)，origin/main。交付状态在远端确认后补记。

## 第三模块：F05 / F06 / F07 / F08

项目full/metadata_only/skip贯通原M持久化边界，原库delivery逐项确认、共享judge/index退避、显式取消和源代/轮换恢复已接线。本阶段16项集中验收通过，具体缺口定点修复；累计20个当前消费者有对应GREEN记录。受控本机链路、取消与实际指南回读见[阶段验收](openspec/changes/resilient-capture-delivery/verification.md)。

已上传模块：[88aa8fb2](https://github.com/wblxr408/ContextRail/commit/88aa8fb2670c93624ec52db770b2a3bc854b10fd)，origin/main；GitHub精确回读确认。

## 下一阶段

继续D03 / D04 / D06：关键字面证据、摘要提交校验和来源变更后的派生页更新；沿用整阶段验收后提交上传的约定。
