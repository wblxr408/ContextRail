# Tasks

## 1. 导出权威与原子发布
- [x] 1.1 A03：新增消费者 RED；捕获有界 bundle 副本、复用原 validator、单向持久化完整来源和不可变导出；重送与修订验证。
- [x] 1.2 A03：原子提交派生 task、证据和快照；验证实际存储失败全部回滚，以及 task/路径/旧 revision 拒绝。

## 2. 必需要求与接收
- [x] 2.1 D02：有效 goal/constraint/always_load 固定编译，具体 ID 超限说明；源变更与旧导出拒绝冒充当前，历史原文可读。
- [x] 2.2 A03：复用原 Bridge 逐项 receipt 校验，覆盖缺项、旧 revision 与完整有效声明；保留语义只是接收者声明的边界。

## 3. 入口与交付
- [x] 3.0 A07：新增当前消费者 RED，提供 status/checkpoints/search/continue/explain、任务隔离、分页和可复制精确恢复 argv；不替用户选择最新快照或创建聊天。
- [x] 3.1 新增三个 CLI、文档和 TODO 完成证据；仅执行本批当前行为用例、规范与构建检查。整体持续实现目标保持 active，继续后续依赖。结果见 verification.md。
