# Design

## Context

详细来源与已有能力去重见 ../../../docs/claude-mem-self-use-todo.md。上游固定 b80b29bc49d08a71312f44b8feacb2e7585edff4，已有 63 份研究切片。原 M/main、Bridge 要求与 Rail task/version 已有实现；E04/E07 继续 causal-recovery-history。

用户已明确：优先稳定性、故障解释、压缩后补注入、文件相关历史、交付物入口；完整界面和更多客户端后置；尽量直接复用现成代码。修订 3：本轮已进入第一阶段实施，后续阶段仍为规划。

## Goals / Non-Goals

**Goals:**

- 首批在 Codex/Claude 现有入口减少排错、重复提问和找资料步骤。
- 上游主体代码尽量保留，由本项目提供符合它接口的数据/宿主适配。
- 交付物可在聊天、CLI/MCP 直接找到与打开，页面不是依赖。

**Non-Goals:**

- 本轮仅实施第一阶段；不展开补注入、文件历史、交付物、完整页面和更多客户端。
- 不将原语言差异当作全部重写的理由，也不为了复用小模块默认运行整套 claude-mem。
- 不先做完整 My Work、更多客户端、云同步或自动整理个人目标。

## Decisions

### D1 优先原代码，其次调整本项目接口

复用顺序为：原模块/源码切片直接使用 → 我方接口适配 → 最小补丁 → 有具体理由才移植。保留 TypeScript/JavaScript 是允许的，不再将 Python/HTML 重写设为默认路线。

本阶段用 contextrail/_vendor/claude_mem 隔离固定源码切片，Python 模块提供适配；实现前明确原函数、最小依赖闭包、运行/构建方式、补丁和消费者。原 M/R 可以提供上游期待的数据格式、目录/日志接口、by-file 查询和持久化接口。若采用移植，应说明直接复用不可取的具体平台、资源或契约原因。

小模块可按需调用或接既有进程；所需运行时只属于对应功能，不默认扩成常驻服务。本阶段采用 Node 22.6+ 的 --experimental-strip-types，按需短进程执行可擦除 TS；不引入 npm 包、Bun、HTTP 服务或后台 worker。运行成本限于每次调用的子进程；未做内存或费用基准，不推断性能收益。原实现需 Bun/数据库/worker 的部分被切除，直接保留被消费的纯函数和配置事务主体。

### D2 权威边界保留，接口允许改变

Bridge 保留要求权威；R 保留原文、task/version/snapshot/lease；M/main 保留长期知识。原接口可包成上游需要的结构，不能因此复制第二个任务/记忆权威。原子注入 gate 和产物投影等辅助记录优先放原库或既有控制区域。

源码修改先在可审阅 integrations/engineering_memory 或 Rail checkout 完成，部署沿用原 owner；不把已安装目录作为唯一源码。用户操作、历史正文和当前要求继续分开。

### D3 稳定性与故障解释不依赖界面

CM01 先登记实际将使用的代码；CM03 优先直接使用 replaceOwnedFiles 主体，我方给出自有文件/片段、安装回执与日志。多文件失败保留逆序恢复和冲突信息；只处理 Codex/Claude 本批接线。CM14 随实际路径处理中文、空格、argv、占用和受管范围，不做全环境治理。

CM02 以原 jobs/control/defer/index/prepared 数据驱动上游错误分类与提示函数，在当前聊天/status/MCP 直接返回；不要求 Web 页面、第二个状态账本或模型调用。CM04 只读生效来源和恢复预览可随故障定位接入，完整设置编辑页面后置。

CM12 连接原显式记录/text recall/deterministic brief 与上游 save-memory 输入输出，在模型不可用时保留原文和待归纳状态。已有队列、退避、共享模型/资源预算和配置备份直接复用；全面数据备份恢复归后续 CM13。

### D4 压缩后补注入直接保留上游主流程

复用 post-compact、session-marker-files、codex-compaction-gate。由我方提供配置路径、日志、精确 client/native_session/task/base_snapshot 与原 packet。实际支持的事件契约在实施时核对；未知事件明确 unavailable。

上游 mark/take 作为主体，但删除标记不证明送达或消费；本地保留 pending/prepared/returned 和未知，失败后可恢复。重复事件不能反复灌入，失效快照不选同项目最新替代。只要求 Codex/Claude 的适用主会话路径；其他客户端后置。

### D5 文件历史沿用 handler，我方提供查询适配

优先保留 file-context、路径生成/格式器、file-context-dedupe。现有 query/timeline/file Ref 适配成上游 by-file 结果；gate 持久化映射到既有控制/存储。用 session + 已解析文件 + source revision 抑制重复。

仅对具体冲突做补丁：不采用全文件读取 deny；复杂 PowerShell 路径不猜；可选历史失败保留普通读取；必需要求失败仍走原 currentness 规则。自动采集仍由原监听器负责，不再写一套工具正文采集。

CM07/CM09 需要时提供现有 CLI/MCP 邻域与详情参数，不等待 SessionDetailPage。E04/E07 未交付能力继续原 change，不复制任务。

### D6 交付物入口先交付，完整界面后置

CM10 优先保留 work-product.mjs 中产物字段、类型、URL 与去重函数，我方把原 task_state/artifact/宿主回执转为它需要的输入，并提供有界 CLI/MCP 列表与详情。当前聊天可直接展示返回链接/路径；绑定 task/source/revision，缺失、改变和发布声明分别处理。

CM10 不依赖 CM08、CM11、CM21，也不要求 HTTP/React/Bun worker 整体可用。先显式登记，保留用户文件原位置；不自动扫描任意目录或复制。链接存在不证明发布成功，打开入口不触发外部写入。

CM08 完整 My Work、CM11 人工查看、CM21 页面连接提示列为后续可选范围。实际需要时优先保留上游 React 页面和状态映射，由我方匹配 API；取消默认 Python/HTML 重做整页的决定。已有 resource 工具适配不要求改为新界面。

### D7 后置依赖不得阻塞首批

完整 UI 的浏览/查看/连接规格仅在该界面显式启用时适用。更多客户端只在用户实际使用第三客户端并需要同一能力时，选其上游 adapter/installer；本批不做通用客户端框架。

多语言 parser、别名、偏好、强化排序、缓存与目录卡继续原编号候选；CM20 只在实际重复装配成本出现时补，CM13 全面数据恢复不挡首批配置回滚。CM24 随所选功能记录实际当前行为，广泛性能/费用实验不自动执行。

## Risks / Trade-offs

- 原模块有较深导入依赖 → 先记录最小闭包和运行成本，再接我方接口；不能把下载当可运行集成。
- 允许原语言带来额外分发成本 → 依条目选择按需方式，复用现有资源控制；没有具体理由不强制翻译。
- 现成 handler 与本地语义不同 → 只补 currentness、来源身份、权限和副作用边界；保留补丁登记。
- 全面界面/维护扩张 → P0 入口独立，后置项无反向依赖。
- 未知被显示为成功 → 故障、准备、返回、模型消费和实际发布分开。
- 本机有在途未提交工作 → 只改本 change 与 TODO，E04/E07 和研究代码保留。

## Migration Plan

本次完成稳定性与故障解释第一阶段，其实现与证据见 verification.md 和 CLAUDE_MEM_STAGE1_USE.md。后续按补注入→文件历史→交付物入口推进，先固定本批行为契约并复用原代码；每阶段同步实际来源、依赖、改动和可观察完成证据。配置/部署仍经原 owner，保留独立停用和恢复材料。

更多客户端和完整页面没有首批交付前置地位。已尝试发布环境的 cloud exec，但服务返回 no cloud environments are available for this workspace；实际执行在本地隔离 worktree，未创建云端任务。

## D8 第一阶段实际适配决策

- 固定上游 b80b29bc49d08a71312f44b8feacb2e7585edff4（Apache-2.0）。逐文件 provenance 列出 owned-file-install、atomic-json、observer-health 与 save-memory 的保留主体和补丁；归因/许可随 wheel 包含。
- 技术选择遵循用户直接复用偏好。整体 worker/Bun/Chroma 会重复现有 M；全译 Python 会丢失直接复用，因此选按需 Node JSON runner。依据 [Node 原生 TS 文档](https://nodejs.org/api/typescript.html) 和 [child_process 文档](https://nodejs.org/api/child_process.html)，使用可擦除语法、显式扩展名与 argv，Python shell=False，Windows 隐藏控制台；Node 缺失明确 unavailable。
- 配置回执只保存自有 MCP 值/精确 TOML 块和 Hook 组。既有等值但无回执条目保持未认领；不按名字推断归属。新安装开始形成回执，升级/移除必须与原回执一致。全文件替换复用上游事务；原备份目录补 restore-plan.json。
- 状态只读原 jobs/job_control/meta 与 task index/prepared；原字段存在才分类。未知 provider 字符串不进入提示，过期等待不证明恢复，不执行健康探测或重启。project.sources 只从可信 CLI/MCP 宿主挂载；原 raw capture、model consumption 边界保留。
- memory-save 只接显式 user 来源，调用原 MemoryRuntime.retain(use_models=False)，相同稳定来源重放一个效果；返回源文件和已有确定性摘录/text recall 接口。该入口不调用 observer、embedding、语义归纳或新记忆库。认证失败等原因仅在原队列真实记录相应种类时可解释，不通过文本猜测新增权威状态。
