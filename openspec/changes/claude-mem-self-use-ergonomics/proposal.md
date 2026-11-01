# Proposal

## Why

用户当前主要在 Codex/Claude 中执行长任务，最需要减少故障排查、压缩后重述、文件历史查询和找产物的步骤。本次按用户明确选择，把稳定性和原生入口提前，完整界面与更多客户端后置，并优先直接复用 claude-mem 原代码。

## What Changes

- 用户追加要求实测本机内存并在有压力时优化：减少无恢复状态和非文件操作的临时 Node 调用，保留原服务、上游恢复主体与真实文件处理。

- 用户追加现有 Codex/Claude 启用：迁移已有 v1 生成接线，保留全局 Hook 与单一记忆服务，完成原生实际生效验证和去重。受管接管必须显式提供旧布局作为证据。

- 修订 4：用户已要求完整完成 A–D，并确认后置项维持按需；当前交付稳定性、故障解释、补注入、文件历史和交付物入口。
- 首批顺序：来源/配置稳定性与故障解释 → 压缩后补注入 → 文件相关历史 → 当前聊天/CLI/MCP 交付物入口。
- 保留原 24 个 CM 编号，P0 8、P1 6、P2 9、按需 1；旧 72 项只关联增量，不重做已有功能。E04/E07 继续原进行中 change。
- 复用规则改为原模块/源码切片直接使用 → 本项目接口适配 → 最小补丁 → 有具体理由才移植。允许所选模块保留 TypeScript/JavaScript，不预设全转 Python。
- 交付物列表、来源和可打开链接先用现有入口交付，不依赖完整 My Work、人工查看页或页面连接模块。
- 完整 My Work/设置编辑页、更多客户端、分组与其他扩展后置；需要时优先保留上游页面/adapter，再让本项目匹配其接口。

## Capabilities

### New Capabilities

- native-context-assistance：首批覆盖支持事件下的精确补注入、文件相关历史与无模型记录；本批聚焦 Codex/Claude。
- personal-work-view：首批交付无完整页面依赖的产物导航；完整任务界面、人工查看和页面状态仅后续显式启用时适用。
- local-client-maintenance：首批解释现有故障和配置来源、可靠更新/撤销受管接线；完整数据备份恢复扩展按实际缺口处理。

### Modified Capabilities

无。沿用已有 change 的已交付行为边界。

## Impact

规划源位于 D:/ContextRail；本阶段在隔离任务分支实现 native_clients/local_status/原 M 接口，固定 TS 切片位于 contextrail/_vendor/claude_mem。原 main、Bridge 要求和 R task/version 权威保留，可以调整本项目接口以承接上游代码。本阶段选择按需 Node 22.6+ 原生类型擦除，无 npm/Bun 构建或安装，不新增默认采集器或云服务，也不进入更多客户端建设。
