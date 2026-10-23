# Tasks

## 1. 原生证据流

- [x] 1.1 以用户无final崩溃重开消费者取得RED→GREEN，接入纯归一化、F01认领与原游标原子提交；记录context-mode映射移植及许可，补使用指南。
- [x] 1.2 补齐双端tool call/result/assistant/lifecycle、来源时间状态和持久化回读；以本批角色隔离、缺时间、凭据/省略、重复/冲突与写失败回滚验证，更新指南。

## 2. 任务导入与部署

- [x] 2.1 实现可信Rail event-import及来源manifest，验证workspace/session/client选择、原子/租约/冻结、重复导入、字节分页及现有MCP精确回读，补使用指南。
- [x] 2.2 运行本批行为及构建/OpenSpec检查，备份并增量部署原M服务，以真实native记录明确补读和任务回读验证，更新E01 TODO与验收。
