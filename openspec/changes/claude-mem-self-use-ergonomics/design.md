# Design

## 本地界面技术与边界

用户已明确启用原后置界面。复用固定 b80b29b 的 React My Work/Viewer、纯映射/连接状态、Feed/SessionDetailPage 和生产构建样式；最小源码补丁去掉不适用云/推广/遥测和原账户设置，保留开源声明。上游前端构建一次为静态文件，浏览器原运行时；既有 Python/SQLite 原接口承担本地兼容 API。与完整 Bun/worker/Chroma 相比，该组合复用原 M/R 并避免第二套知识/任务权威与采集进程；与新 React 界面重写相比，保留现成布局和映射边界。保留两套 UI 的构建依赖和源锁，运行期不依赖 npm 或开发服务器。浏览器 DOM 与网络 API 依据 React 官方 useEffect 清理契约；视图继续继承上游样式，Kombai Gallery 仅作仪表盘布局检查参考。

Python ThreadingHTTPServer 仅监听127.0.0.1，验证Host、Origin与JSON写入边界，不提供任意路径/命令/模型参数。记忆只读原 main vault 和原 normalized_events；既有任务/状态/产物/文件查询均走原 ContextTools。显式人工查看仅在原 R 保存 UI 元数据，绑定任务版本和产物实时元数据；它不意味着验收通过或改变租约。设置调用原 toggle_client 并用配置版本拒绝过期更新，不编辑全局账户/模型/Hook。

界面进程按需启动，SSE仅在页面连接期间读现有状态，不注册新监听器、学习器或计划任务。空闲无页面自动退出，原 M/R 不受影响。UI传输在线与来源可读分别表示，保留上游最后已知视图。字体与源码许可随静态文件交付；依赖体量只影响构建期，不把整套node_modules带入运行包。

## 本机内存优化增量

用户要求判断实际内存压力并在有压力时优化。2026-11-01 本机约 15.2 GiB 可见内存，可用约 2.3 GiB；新增 Rail 未常驻，但普通 Hook 实测进程树峰值 94–109 MiB，重复文件读取约 136 MiB。仅优化已有临时子进程，不关闭其他应用或原 main/ATR。

对原上游 marker 的 has 增加负向快速路径：宿主 key 固定为 canonical JSON 数组，按固定源码的 sha256 命名定位同一 marker。只有确定不存在时直接返回 false；存在或无法确定时仍调用原 TS has，mark/take 不移植。原数据库 pending/prepared 查询仍执行，避免遗漏 marker 消费后未确认返回的恢复状态。对上游不支持的工具名跳过 file-paths Node，真实文件入口仍使用原解析/格式器/持久 gate，不增缓存或监听器。

## 现有客户端启用增量

用户追加授权本机启用。旧生成布局是显式迁移凭据，只有当前值与其中服务器/Hook 精确一致才接管；缺少或人工改变的旧项报冲突，整批预规划后仍复用 replaceOwnedFiles。迁移不碰全局 Hook、模型、凭据或原生信任库。v1 稳定路径保持，备份覆盖项目配置及受管回执。

当前原生主会话来源可在选定任务 registry 显式启用 current_hook 绑定模式：从可信 Hook payload 获取精确 client/session，临时加入有界查询绑定，不写白名单、不猜测最新日志。子会话、工作区不匹配、禁用绑定不会获得该能力。默认仍是原静态白名单。

启用验收只运行本次迁移/新会话目标的最小行为 TDD 与用户要求的真实双端流程。原生会话使用既有模型/账号和正常权限；保留 stdout_flushed 与模型实际观察的区别。

## Context

详细来源与已有能力去重见 ../../../docs/claude-mem-self-use-todo.md。上游固定 b80b29bc49d08a71312f44b8feacb2e7585edff4，已有 63 份研究切片。原 M/main、Bridge 要求与 Rail task/version 已有实现；E04/E07 继续 causal-recovery-history。

用户已明确：优先稳定性、故障解释、压缩后补注入、文件相关历史、交付物入口；完整界面和更多客户端后置；尽量直接复用现成代码。修订 4：用户目标已扩展为完整完成 A–D 四阶段；用户明确确认后置项仍按需启用。

## Goals / Non-Goals

**Goals:**

- 首批在 Codex/Claude 现有入口减少排错、重复提问和找资料步骤。
- 上游主体代码尽量保留，由本项目提供符合它接口的数据/宿主适配。
- 交付物可在聊天、CLI/MCP 直接找到与打开，页面不是依赖。

**Non-Goals:**

- 本轮完整完成 A–D；完整页面、更多客户端及后置 P1/P2 维持按需启用。
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

## D9 原生补注入与原事件身份

2026-11-01 核对官方 Codex hooks schema 和 Claude hooks 文档：PostCompact 不能承载 additionalContext，SessionStart(compact)/PreToolUse/PostToolUse/UserPromptSubmit 可返回。主会话原 SessionStart、PostToolUse、UserPromptSubmit 接上游 postCompactHandler 与 mark/take；不注册新的采集器。

原 QueryBinding 必须明确 client/native_session/workspace/M home。触发记录 pending，不读取/归纳 compact_summary；原监听器 normalized_events 中 context.compacted 的 event_id+sequence 是真实 cycle，未捕获到则明确 awaiting_original_compaction。来源重放不会重新登记 returned，下一次真实 cycle 可以再次补入。

在原 Rail DB 中以 scope/client/native_session/base_snapshot/source_event 保存 prepared/returned 和 60 秒 attempt claim，既有 events 保存审计。上游 marker 只用于触发，不是权威送达账本。prepare 后进程退出，原 DB 的未确认状态可在 claim 过期后重试；返回后 stdout flush 才记录 returned，模型消费始终未知。发送与回执之间的进程损失具有传输不确定性，可恢复重试不承诺物理 exactly-once。

必需 packet 调用原 Bridge.refresh(exact base) 和现有预算渲染，不选择其他项目最新快照。源失效、预算失败保留 pending 并阻止当前继续；新 attempt 通过 CAS 防止迟到回执覆盖。新的受管 Hook 事件继续同一配置事务与精确撤销，不更改原生 trust。

官方来源：[Codex SessionStart schema](https://github.com/openai/codex/blob/main/codex-rs/hooks/schema/generated/session-start.command.output.schema.json)、[Codex PostCompact schema](https://github.com/openai/codex/blob/main/codex-rs/hooks/schema/generated/post-compact.command.input.schema.json)、[Claude hooks](https://code.claude.com/docs/en/hooks)。这是源契约与真实命令 stdin/stdout 验证，不声称当前打开的桌面会话已热更新。

## D10 文件历史使用原索引与原子 gate

直接保留 file-context 的 handler、分组/排序、路径形式和时间格式，移除全文件 deny/智能工具重定向、重复 memorySearch 与 worker/Bun 创建。Python 只把既有 M/R timeline 变成上游 ObservationRow，原路径函数生成绝对、工作区相对及 home 相对查询形式。未知发生时间显示 unknown，不补造历史时间。

原 file-context-dedupe 的 GATE_TABLE_DDL/CLAIM_GATE_SQL 由原 R SQLite 连接执行。scope+client+native_session 编入 session key，规范文件路径+原来源版本集合映射为单调 epoch；不复制记忆正文到 gate，不按正文 hash 判重，不自动 TTL 清扫。最终渲染满足原字节/字符预算后才以原 SQL 原子认领。

PowerShell 与上游 Bash shell-quote 不是同一语法。本机仅使用原生 PowerShell AST 解析单条 Get-Content 的字面路径，不执行输入命令；变量、管线、表达式和不支持参数明确跳过。其余结构化 Read/Edit 与上游 MCP path 提取保持直接路径。非 Windows 解析器缺失时只跳过可选增强。

context.file_history/history_details/history_neighborhood 和对应 CLI 沿用固定宿主绑定。旧三源查询的 context.get/memory.stream_get/basic-memory.read_note refs 可直接进入详情；M note 不提供精确历史版本或事件锚点时明确标注，不能假造其时间邻域。部分来源不可用不吞掉其他可读项，普通浏览不领取任务、不改租约。

## D11 交付物沿用原 artifact 与 task_state

work-product.mjs 整个原模块保持不改，实际消费 productsFromFields/safeWebUrl/fileKind/mergeProducts。显式宿主登记为原 Rail deliverables/<id>/record 版本化 artifact，同一 fencing 事务中核对 source Ref、expected revision 和根目录，原 events 保存登记事件；未建立第二个产物知识库或任务状态权威。原 task_state 字段作为声明投影，不把已有源接口重写。

打开入口使用绝对路径和 HTTP(S) 链接；不部署上游 HTTP 路由、bucket 复制或发布器。旧 record_ref 可精确回读，来源版本与当前头不同明确标历史。外部文件只观察文件身份/大小/mtime/ctime，metadata_matches 不等于正文验证；缺失/改变保留登记位置。仅 URL 永远不提升为发布成功。

## D12 当前 MCP 环境的薄兼容修复

真实新交付物 stdio 调用发现本机共享 Python 的 SDK 2 不再提供 Server.list_tools 装饰器，旧挂载在处理业务前退出。比较局部隔离强制 1.x 与薄兼容：前者令已有客户端路径失效并引入额外运行环境；后者仅在注册/字段命名边界分支，原读取/参数验证/计量共用，因此选择后者。MCP optional 范围为 >=1.28,<3，未更改全局依赖。功能、部署兼容、维护和可靠性是决定维度；速度/内存/费用未做基准，生态/安全没有新增第三方服务器或权限，语言/库退出策略保留原官方 SDK。

SDK 2 采用官方 on_list_tools/on_call_tool、snake_case types 和 ctx.request_id；1.x 保留原方法。当前 SDK 2 与隔离 SDK 1.30 均用真实工具发现、列表、精确详情和拒绝 scope override 验证；没有以握手/启动替代业务。依据 [官方 migration](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md)。
