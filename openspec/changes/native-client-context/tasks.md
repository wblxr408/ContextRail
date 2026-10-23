# Tasks

## 1. 有界读取与运行时视图
- [x] 1.1 新增当前消费者 RED；owner-fenced reader、MCP context.packet 与两端 reader 冷页读取/非法覆盖/写权边界。
- [x] 1.2 明确 base 的 runtime refresh、来源/freeze/lease 校验、同版本幂等与派生快照导航。

## 2. Hook 与布局
- [x] 2.1 可信绑定、两端字段归一化、记忆命令一次调用、完整预算组合/失败可见与禁用透传；不读取 transcript。
- [x] 2.2 client-prepare/toggle 与单一源码 runner，生成 Codex/Claude 配置并验证跨目录实际使用；完成可审阅使用文档。

## 3. 客户端验收与交付
- [x] 3.1 两端分别实际调用任务包/冷页，验证已有记忆组合；必要原生审阅或模型渠道缺口保留，不冒充完成。
- [x] 3.2 新增当前行为用例及必要构建/规范，按实际证据勾选 A06/A08，记录剩余工作；整体目标不提前 complete。
