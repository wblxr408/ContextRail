# claude-mem → context-bridge / ContextRail 自用集成 TODO

修订 3：2026-11-01（Asia/Shanghai）。第一阶段进入实现；实际交付与边界见 CLAUDE_MEM_STAGE1_USE.md 和对应 OpenSpec verification.md，后续阶段仍为规划。

**优先顺序：稳定性与故障解释 → 压缩后补注入 → 文件相关历史 → 交付物入口。** 完整 My Work/设置页面、人工查看/收藏界面与 Codex/Claude 之外的客户端后置。交付物必须先在当前聊天、CLI 或 MCP 中可找、可回读、可打开，不等待页面完成。

**复用方向：先让本项目匹配 claude-mem 现成模块的接口，尽量保留原语言和主体代码；必要时少量补丁，有具体理由才移植。** 保留 TypeScript/JavaScript 模块是可行选项，不再默认全部改写成 Python，也不要求上游为本项目开发专用支持。

用户本轮原话：“按我的使用习惯重新调整TODO，优先稳定性、故障解释、压缩后补注入、文件相关历史和交付物入口，完整界面和更多客户端放后面，尽量直接复用claude-mem现成代码。”

## 研究依据与边界

上游为 [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem)，固定 [commit b80b29bc49d08a71312f44b8feacb2e7585edff4](https://github.com/thedotmack/claude-mem/commit/b80b29bc49d08a71312f44b8feacb2e7585edff4)，提交时间 2026-11-01T05:21:15Z，package.json 版本 15.8.0。已定向取得 63 份源码/许可文件作为研究副本，没有运行它的安装器、脚本、模型、服务或测试。源码目录是按关注机制选择的切片，不是整个仓库审计。

已对照 D:/ContextRail 的实现、旧 72 项 TODO、OpenSpec 交付记录、已安装 Bridge 契约与工程记忆 app。原生 Basic Memory 查询本会话失败后，沿用已直接读取的本地 main 笔记与当前实现，不宣称本轮 MCP 召回成功。后续状态以实际现场为准。

本机存在进行中的 causal-recovery-history，以及其他未提交修改。本次不修改这些源码或判断其验收结果；E04/E07 继续归属原 change。目前该 change 的 1.1/1.2 标完成，1.3/1.4 未完成。旧总表顶部多段累计数和部分说明保留历史，不能据此覆盖较新的源码/交付记录；本轮不改旧 72 项计数、不重勾完成项。

## 当前能力去重

| 上游功能 | 本地已有依据 | 本轮处理 |
|---|---|---|
| 自动采集、归纳、长期记忆 | 原 M listener、memory_runtime、main Markdown、既有预算 | 复用；不装第二个 observer/记忆库 |
| 三源搜索与去重缓存 | federation.py、query_sources.py；C06/C08 已交付 | CM07 只补导航/展示 |
| 时间线与邻域过滤 | timeline.py、timeline_index.py；E08 已交付 | CM09 只补现有入口导航，页面后置 |
| 任务状态与在途动作 | workscene.py、Store actions/lease；E02/E03/B08 已交付 | CM08 投影原状态，不复制上游 TODO 库 |
| 必需要求、末尾纠正、精确恢复 | bridge.py、intent.py、recovery.py；A03/D02/E05/E06/I02 已交付 | CM05 必须继续走同一要求权威 |
| 分层简报、loss 与原文回读 | layered.py、loss.py、hit_windows.py；D01/D05/C05 已交付 | 不再列基础分层读取功能 |
| 离线队列、退避、日志轮换、脱敏 | ingest_delivery / stream_cursor / capture；F01–F08 已交付 | CM02 只补原因显示；不移植整套 hook-spool |
| 客户端合并、独立停用、原生注入 | native_clients.py；A06/A08 已交付且后续补验关闭缺口 | CM03 补升级/撤销；不能沿用旧 README 的未激活结论 |
| 摘要/知识页版本、当前与历史分离 | derivation.py、derived_pages.py、M runtime；D03/D04/D06 已交付 | 复用，不新增摘要/反思引擎 |
| 请求/工具回读计量 | accounting.py、policy.py；D07/H03/D08 已交付 | CM24 只补本批实际收益材料 |
| 错误尝试链与原生历史补读 | error_history.py、native_history.py 和 causal-recovery-history 在途 | 不新建平行 E04/E07 |
| Python/Markdown 结构分块 | chunking.py:StructuralChunker | CM15 只增加多语言与 outline/unfold 交互 |

## 接入归属

- **R**：D:/ContextRail/contextrail，管理任务证据、工作集、引用、快照、租约和现有入口；完整个人界面后置。
- **M**：先改 D:/ContextRail/integrations/engineering_memory 中可审阅适配源码；已安装 C:/Users/wblxr/.codex/services/engineering-memory/app 用于核对与后续独立部署，不能直接作为唯一源码维护位置。
- **B**：当前 context-bridge 技能的要求账本、workspace claim、handoff/resume 与 checkpoint 生命周期。涉及独立 Bridge 仓库时在其实际 checkout 修改，本轮未建立新 checkout。
- **main**：C:/Users/wblxr/basic-memory。长期知识权威继续是 Markdown；R/M SQLite 的角色保留现有边界。
- 下文“拟增”明确表示新文件建议。模块名是接入设计，不是已有实现声明。

## 复用顺序与首批范围

优先级按当前使用习惯调整为 **P0 8 项、P1 6 项、P2 9 项、按需 1 项**，仍保留原 24 个 CM 编号。原任务关联继续只记增量，旧 72 项不重复计数。

| 优先级 | 含义 | 本批条目 |
|---|---|---|
| P0 | 首批日常可靠性与省步骤 | CM01/CM02/CM03/CM05/CM06/CM10/CM12/CM14 |
| P1 | 支撑既有入口，按首批实际需要接入 | CM04/CM07/CM09/CM13/CM20/CM24 |
| P2 | 核心体验可用后再按实际频率选择 | CM08/CM11/CM15/CM16/CM17/CM18/CM19/CM21/CM22 |
| 按需 | 特定材料浏览需求 | CM23 |

复用决策依次采用：

1. **原模块直接使用**：保留原 TypeScript/JavaScript 与必要依赖，本项目向模块提供它期待的数据和接口。无需因为主项目使用 Python 就翻译模块。
2. **本项目接口适配**：查询、任务、路径、日志、gate 持久化与产物输出按上游调用形状封装，长期知识、要求和 task/version 仍由现有 owner 管理。
3. **最小补丁**：只修改确有冲突的分支，例如过期来源处理、文件读拦截、隐式复制和外部发布推断。记录与固定版本的差异。
4. **有理由才移植**：仅当依赖闭包/启动成本、平台支持或本地关键契约使直接使用不合适时，说明具体原因后移植所需部分；“用了 Bun/React/TypeScript”本身不是重写理由。

所需原语言构建/运行时可作为相应模块的局部依赖；按真实导入图记录最小闭包、运行方式、启动/内存成本与升级责任。第一阶段现选择按需 Node 22.6+，不安装 npm/Bun、不增加默认常驻后台，具体来源与补丁见阶段指南。核心已有 Python/SQLite 和 main 权威继续保留，可调整边界接口以接入上游代码。

原生 Hook、文件历史和产物入口优先沿用上游代码。未来确有完整界面需求时，保留 React 页面/组件，让本项目后端匹配其 API；取消此前默认以 Python/HTML 重做整页的路线。

## 首批依赖：不让界面挡住核心体验

| 事项 | 真正前置 | 可直接交付的入口 | 明确后置 |
|---|---|---|---|
| 稳定性 | CM01，原配置回执与备份 | Codex/Claude 现有接线、CLI | 更多客户端、全环境治理 |
| 故障解释 | 原 jobs/control/index/prepared 状态 | 当前聊天、status/MCP | CM08/CM21 的页面 |
| 压缩后补注入 | CM03、原事件能力、Bridge 精确绑定 | 支持的原生主会话 Hook | 完整页面 |
| 文件相关历史 | 已完成 query/timeline/file refs；受限路径处理 | 原生文件事件＋已有查询 | 会话详情页面 |
| 交付物入口 | 原 Store、产物声明/宿主结果、合法路径 | 当前聊天链接、CLI/MCP 列表与回读 | CM08/CM11/CM21 |

P0 的资源与 Windows 改进只覆盖本批实际触及的模块，不扩成全面性能优化。已有队列、退避、共享模型与资源预算保持复用。CM13 的全面数据备份恢复扩展、CM20 的额外渲染缓存按实际缺口再做，不能成为首批入口必须等待的大工程。

## TODO 总览

| ID | 优先级 | 事项 | 旧任务/已有能力关系 |
|---|---|---|---|
| [CM01](#cm01) | P0 | 固定上游源码切片与复用登记 | 既有 THIRD_PARTY_NOTICES 与 _vendor 布局 |
| [CM02](#cm02) | P0 | 把现有采集、学习与注入状态解释成人能处理的故障 | H01 已完成，F07 已完成；补可操作原因 |
| [CM03](#cm03) | P0 | 受管配置的升级、失败回滚与精确卸载 | H05；复用 A08 已有布局 |
| [CM04](#cm04) | P1 | 从现有入口解释生效配置与恢复预览 | H04；复用 project.sources 与 H02 |
| [CM05](#cm05) | P0 | 原生压缩后补回精确任务入口与相关记忆 | A06 已完成；与进行中的 E07 分工 |
| [CM06](#cm06) | P0 | 读取或编辑文件时展示该文件的相关历史 | C06/C08/E02 已完成，复用其检索与来源 |
| [CM07](#cm07) | P1 | 把三源搜索接成索引→邻域→详情的连续操作 | C06/E08/H02 已完成 |
| [CM08](#cm08) | P2 | 完整任务总览与 My Work 界面（后置） | E03/H01/H02 已完成，复用当前事实 |
| [CM09](#cm09) | P1 | 任务与会话详情的原生入口钻取（页面后置） | E08/H02；E04/E07 继续原 change |
| [CM10](#cm10) | P0 | 聊天、CLI/MCP 中可直接打开的交付物入口 | E03、I04；复用已有产物原文 |
| [CM11](#cm11) | P2 | 人工已查看标记与版本变化后的重新待查看 | 与 E03 完成事实分离 |
| [CM12](#cm12) | P0 | 无模型时仍能显式记事、看来源与生成简报 | G06；复用现有 record / text recall / deterministic brief |
| [CM13](#cm13) | P1 | 明确范围的备份、恢复与可重建索引清单 | H06/H07；复用原生命周期 |
| [CM14](#cm14) | P0 | Windows 的命令、路径和重命名失败处理 | H08；复用 process_identity/native_clients |
| [CM15](#cm15) | P2 | 多语言符号大纲与按符号展开 | 已有 chunking.py / C05，补语言与交互 |
| [CM16](#cm16) | P2 | 显式项目别名与迁移目录后的历史查找 | A02 已完成，补 aliases |
| [CM17](#cm17) | P2 | 宿主原生记忆的定向只读查找与显式导入 | G08；E07 的会话历史继续原归属 |
| [CM18](#cm18) | P2 | 有出处的偏好视图与规则差异草稿 | G01/I07/G05 |
| [CM19](#cm19) | P2 | 重复确认的经验参与候选排序 | G07；与 I05 区分 |
| [CM20](#cm20) | P1 | 稳定简报缓存与动态时间标签分离 | C08 已完成；只补渲染路径 |
| [CM21](#cm21) | P2 | 完整页面的断线、陈旧数据与来源提示（后置） | H01/H02 的界面补强 |
| [CM22](#cm22) | P2 | 常用任务、查询和产物的手动收藏与分组 | I04；复用已有项目/任务身份 |
| [CM23](#cm23) | 按需 | 生成可读的目录历史卡片 | H02/I07；复用 F03 派生来源过滤 |
| [CM24](#cm24) | P1 | 用真实自用任务评估这批增量的收益 | I08；复用现有 evaluation/accounting |

## 逐项实施说明
<a id="cm01"></a>

### CM01｜固定上游源码切片与复用登记

- [x] **P0；新增工程接线。** 挂接：既有 THIRD_PARTY_NOTICES 与 _vendor 布局。
- **自用效果**：后续实现能直接取用已读代码，避免再次调研或误拿主分支新行为。
- **已有能力与缺口**：Rail 已有 ai-memory / context-mode 固定来源和许可；本次只增加 claude-mem 来源，沿用同一登记文件。
- **上游实现**：[LICENSE](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/LICENSE)；[NOTICE](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/NOTICE)；[src/shared/owned-file-install.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/shared/owned-file-install.ts)。
- **优先复用方式**：保留固定版本上游模块、LICENSE/NOTICE 和需要的依赖文件；优先原语言执行并由本项目提供路径、日志、状态与存储接口适配。仅当运行/维护成本或本地契约有具体冲突时才移植，并写明理由。
- **接入位置**：D:/ContextRail/THIRD_PARTY_NOTICES.md；contextrail/_vendor/claude_mem 的固定源码切片及 Python 薄适配；现有 integrations/engineering_memory/deploy.py；原 M/R 数据权威保留。
- **依赖/前置**：选定切片按需依赖 Node 22.6+；所有后续 CM 项以此作为代码来源前置。
- **完成标准**：每个选定模块登记原文件/函数、依赖闭包、原语言运行或编译需求、本地接口改动和许可；采用 Python 移植时附具体原因；下载研究副本不算已集成。
- **适配边界**：本阶段已直接消费所选 TS 切片，详见 provenance.json；不复制品牌、字体或第三方资产。

- **第一阶段证据**：见 `openspec/changes/claude-mem-self-use-ergonomics/verification.md`；仅勾选本条首批契约，不推断上游整套系统已集成。

<a id="cm02"></a>

### CM02｜把现有采集、学习与注入状态解释成人能处理的故障

- [x] **P0；挂接原任务。** 挂接：H01 已完成，F07 已完成；补可操作原因。
- **自用效果**：看到“没有记忆”时，能知道是没有捕获、排队、资源延期、认证失败还是索引未完成。
- **已有能力与缺口**：local_status.status 已读原库 capture / learning / prepared；listener 与 ingest_delivery 已有退避和错误状态。
- **上游实现**：[src/shared/observer-health.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/shared/observer-health.ts)；[src/ui/my-work/src/live/connection-status.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/ui/my-work/src/live/connection-status.ts)。
- **优先复用方式**：优先保留 observer-health 的错误分类、时长和提示格式函数，通过本项目适配层给它原 jobs/control/prepared 的观测数据；不为复用纯展示函数先重写整套 Python 服务。
- **接入位置**：local_status.py:status；原 M listener/ingest_delivery；拟增 integrations/claude_mem 故障说明模块与状态投影接口；优先从聊天/CLI/MCP 返回。
- **依赖/前置**：CM01；既有原 M/R 状态；不依赖 CM08/CM21，不需要 Web 页面或新模型调用。
- **完成标准**：在现有入口看出未采集/排队/延期/认证失败/待索引/已准备的区别，给出可复制的对应操作；仅凭已观察状态解释，配额不建议重启；未知与模型消费未知保留。
- **适配边界**：不移植 doctor 的版本执行、HTTP health 探测、自动重启或账号跳转；缺现场观测保持 unknown。

- **第一阶段证据**：见 `openspec/changes/claude-mem-self-use-ergonomics/verification.md`；仅勾选本条首批契约，不推断上游整套系统已集成。

<a id="cm03"></a>

### CM03｜受管配置的升级、失败回滚与精确卸载

- [x] **P0；扩展原任务。** 挂接：H05；复用 A08 已有布局。
- **自用效果**：更换源码目录或升级 Hook 后，不留下重复入口，也不影响你自己的其他配置。
- **已有能力与缺口**：native_clients.apply_project_clients 已合并 JSON/TOML、备份、拒绝同名冲突；缺版本迁移、精确撤销和多文件失败恢复。
- **上游实现**：[src/shared/owned-file-install.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/shared/owned-file-install.ts)；[src/services/integrations/CodexCliInstaller.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/integrations/CodexCliInstaller.ts)；[src/npx-cli/commands/uninstall.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/npx-cli/commands/uninstall.ts)。
- **优先复用方式**：优先原样保留 replaceOwnedFiles 的阶段替换与逆序回滚主体，为其日志/文件清单提供兼容接口；受管配置段映射由本项目完成，installer/uninstall 仅选需要的代码片段。
- **接入位置**：contextrail/native_clients.py:prepare_clients/apply_project_clients/toggle_client；拟增 client-remove / client-migrate CLI。
- **依赖/前置**：CM01；原 registry 和自有配置回执；只处理 Codex/Claude，已有备份可直接复用。
- **完成标准**：同布局重用不增项；升级替换自己的旧入口；任一步失败恢复已写部分；卸载只移除确属本工具且未被用户改写的项；人工修改报冲突并保留恢复材料。
- **适配边界**：不照搬按字符串清理全局目录；不自动写原生 trust、不改凭据、不卸载 Basic Memory。

- **第一阶段证据**：见 `openspec/changes/claude-mem-self-use-ergonomics/verification.md`；仅勾选本条首批契约，不推断上游整套系统已集成。

<a id="cm04"></a>

### CM04｜从现有入口解释生效配置与恢复预览

- [ ] **P1；扩展原任务。** 挂接：H04；复用 project.sources 与 H02。
- **自用效果**：不用在 TOML、registry、M 配置和 Hook 输出之间猜哪个设置真正生效。
- **已有能力与缺口**：project.load_project 已记录 explicit/config/default 来源；已有 brief/explain，缺跨 M/R/客户端的统一视图和受限编辑入口。
- **上游实现**：[src/shared/SettingsDefaultsManager.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/shared/SettingsDefaultsManager.ts)；[src/services/worker/http/routes/SettingsRoutes.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/worker/http/routes/SettingsRoutes.ts)；[src/ui/viewer/components/ContextSettingsModal.tsx](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/ui/viewer/components/ContextSettingsModal.tsx)。
- **优先复用方式**：先复用上游设置遮罩/变更字段和 preview 的现成函数，输出接现有 CLI/MCP；完整设置页面保留原组件供后续复用，不默认写 Python 替代 UI。
- **接入位置**：project.py:load_project/describe；native_clients.read_registry；local_status.py；原 M 状态接口。完整设置编辑页随 CM08 后置。
- **依赖/前置**：CM02、CM03；故障定位需要的只读部分随 P0 接线；页面不作为前置。
- **完成标准**：不离开当前入口即可看值来自哪里、何时生效、恢复将加载哪些要求；只读查看不改配置；未来启用编辑时遮罩不覆盖秘密。
- **适配边界**：不把 preview 当历史真实输入；不自动调整模型、额度或全局配置。

<a id="cm05"></a>

### CM05｜原生压缩后补回精确任务入口与相关记忆

- [ ] **P0；新增 Hook 接线。** 挂接：A06 已完成；与进行中的 E07 分工。
- **自用效果**：会话 compaction 后不必等下一次完整用户请求，仍能找到当前要求和回读入口。
- **已有能力与缺口**：当前 Rail host_hook 只接受 UserPromptSubmit；原任务包、owner 校验和原 M 注入可复用。
- **上游实现**：[src/shared/codex-compaction-gate.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/shared/codex-compaction-gate.ts)；[src/shared/session-marker-files.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/shared/session-marker-files.ts)；[src/cli/handlers/post-compact.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/cli/handlers/post-compact.ts)。
- **优先复用方式**：直接复用 post-compact handler、session-marker-files 与 codex-compaction-gate 的主会话流程；让本项目提供数据目录/日志/packet/currentness 接口，保留上游 mark/take 主体，再补本地 pending/prepared/returned 语义。
- **接入位置**：拟增 integrations/claude_mem 主会话 Hook 适配；native_clients.py 事件分发；Bridge packet 与现有 prepared 事件；原 M 注入入口。
- **依赖/前置**：CM01、CM03，CM14 仅覆盖该接线涉及的 Windows 路径；使用现有要求绑定；不依赖完整界面和额外客户端。
- **完成标准**：压缩事件仅登记 pending；下一个可输出上下文的事件补一次；重复事件不反复灌入；旧快照或必需材料不足明确报缺口；当前纠正仍可精确回读。
- **适配边界**：上游 marker 删除早于真实送达，不是模型消费证明；本地按 pending→prepared→returned 记录，不自动续写或接管；不新增多 Agent 架构。

<a id="cm06"></a>

### CM06｜读取或编辑文件时展示该文件的相关历史

- [ ] **P0；新增触发接线。** 挂接：C06/C08/E02 已完成，复用其检索与来源。
- **自用效果**：打开一个文件就能看见之前为何这样改、踩过什么坑，减少额外问一次记忆。
- **已有能力与缺口**：已有文件/场景来源、三源查询、时间线和重复查询缓存；缺 PreToolUse 文件事件与长期记忆的自动关联。
- **上游实现**：[src/cli/handlers/file-context.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/cli/handlers/file-context.ts)；[src/cli/handlers/file-context-dedupe.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/cli/handlers/file-context-dedupe.ts)；[src/cli/adapters/codex-file-context.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/cli/adapters/codex-file-context.ts)；[src/services/sqlite/observations/files.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/sqlite/observations/files.ts)。
- **优先复用方式**：优先保留 file-context handler、路径生成、格式器和 dedupe 原代码；本项目实现 by-file 查询和 gate 持久化适配，返回其需要的记录格式；仅剥离当前无需的读拦截分支。
- **接入位置**：integrations/claude_mem 文件历史适配（拟增）；native_clients.py；timeline/query/file-ref 现有接口；M/R 只保留既有权威库。
- **依赖/前置**：CM01、CM03；复用已完成 C06/C08/E02；必要的 CM14 路径处理；CM05 可先交付，也可共用事件层；不依赖 CM08/CM09 页面。
- **完成标准**：在 Codex/Claude 的支持事件中，读/改文件时收到带来源的短历史；同来源版本不重复，新版本可再补；路径与 worktree 正确；可选历史失败不中断普通读取。
- **适配边界**：不继承上游 whole-file Read deny；shell 路径提取只认支持的结构，复杂 PowerShell 不猜；自动捕获继续由原监听器负责。

<a id="cm07"></a>

### CM07｜把三源搜索接成索引→邻域→详情的连续操作

- [ ] **P1；补现有交互。** 挂接：C06/E08/H02 已完成。
- **自用效果**：一次命中后直接看当时发生的事，再批量回读关键原文，少手工拼参数。
- **已有能力与缺口**：已有 context.query、timeline、batch、get；底层能力不重做，只统一结果导航。
- **上游实现**：[src/shared/progressive-search.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/shared/progressive-search.ts)；[src/services/work-view/request-projection.mjs](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/work-view/request-projection.mjs)。
- **优先复用方式**：保留 ProgressiveBackend/trace/结果格式器原代码，本项目实现后端协议和三源身份映射；直接返回可调用的邻域/详情参数，不先造新搜索引擎。
- **接入位置**：contextrail/federation.py、timeline.py、batch.py、host.py、cli.py；拟增搜索导航格式器。
- **依赖/前置**：CM01 与已有 QueryBinding/timeline/batch/get；可在聊天/CLI/MCP 单独使用，不依赖界面。
- **完成标准**：结果可直接携精确 source/kind/id/revision 进入邻域与详情；批量读取保留逐项失败；空结果、来源失败和详情失效分开；无隐藏模型改写查询。
- **适配边界**：上游 rank 的英文分词不能直接当中文召回算法；其截短 observation 仅能作为显示预览，不能替代 Rail 原文。

<a id="cm08"></a>

### CM08｜完整任务总览与 My Work 界面（后置）

- [ ] **P2；新增展示层。** 挂接：E03/H01/H02 已完成，复用当前事实。
- **自用效果**：少打开几个终端就能知道有哪些活没收尾、下一步该做什么。
- **已有能力与缺口**：WorkScene 与 local_status 已有任务及证据；当前 dashboard 是评测用途，不是日常任务入口。
- **上游实现**：[src/services/work-view/work-state-reduce.mjs](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/work-view/work-state-reduce.mjs)；[src/services/work-view/data-source.mjs](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/work-view/data-source.mjs)；[src/ui/my-work/src/live/to-work-model.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/ui/my-work/src/live/to-work-model.ts)。
- **优先复用方式**：后续实际需要完整界面时，优先保留上游 React 页面、组件与状态映射，让本项目后端匹配其 API；先评估所需依赖，不再预设用 Python/HTML 重写整页。
- **接入位置**：后续保留上游 React My Work 组件与状态映射；我方在原 R/M 边界提供匹配 API，拟增 integrations/claude_mem 的视图适配；不预设 Python/HTML 重做页面。
- **依赖/前置**：首批 CM02/CM05/CM06/CM10 已可日常使用后，再按实际查看频率选择；界面不是交付物入口或恢复的前置。
- **完成标准**：启用时复用上游现成界面，任务来源映射准确，scope 不合并、未知/声明/证据可辨；部署和维护成本有记录；核心入口在关闭页面后仍可用。
- **适配边界**：不复制 work_state_entries 为第二套 TODO 权威；不采用模型估计百分比宣称完成，不接 TeamBrain。

<a id="cm09"></a>

### CM09｜任务与会话详情的原生入口钻取（页面后置）

- [ ] **P1；补现有入口。** 挂接：E08/H02；E04/E07 继续原 change。
- **自用效果**：从任务卡直接找到某次失败、文件修改或最后纠正，不必翻整份日志。
- **已有能力与缺口**：timeline 已按来源时间排序并有 file/category/anchor；native_history/error_history 为进行中实现，不能重报为已交付。
- **上游实现**：[src/ui/viewer/components/SessionDetailPage.tsx](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/ui/viewer/components/SessionDetailPage.tsx)；[src/services/work-view/request-projection.mjs](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/work-view/request-projection.mjs)。
- **优先复用方式**：先将上游 request projection/过滤和结果组织函数接现有 CLI/MCP；完整 SessionDetailPage 后续原组件接 API，不翻译成 Python 页面。
- **接入位置**：timeline.py、hit_windows.py、现有 context.timeline/context.get 与 CLI；SessionDetailPage/CM08 仅后续界面接入。
- **依赖/前置**：CM07 与已有时间线；E04/E07 仍待原 change 交付；CLI/MCP 不依赖 CM08。
- **完成标准**：按文件/错误/会话过滤，展开返回来源窗口；未知时间明确；迟到结果不改原事件身份；实际会话不因同 cwd 被自动拼成一个任务。
- **适配边界**：只改善已有查询的呈现；不新增另一份错误经历库或 transcript 导入器。

<a id="cm10"></a>

### CM10｜聊天、CLI/MCP 中可直接打开的交付物入口

- [ ] **P0；新增结果关联。** 挂接：E03、I04；复用已有产物原文。
- **自用效果**：报告、图片、PR 等结果能挂在对应任务下，隔几天还能找到。
- **已有能力与缺口**：当前 task state 与 Ref 能保存来源；缺统一的 deliverable 视图和失效位置说明。
- **上游实现**：[src/services/work-view/work-product.mjs](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/work-view/work-product.mjs)；[src/cli/deliverables-capture.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/cli/deliverables-capture.ts)；[src/services/sqlite/deliverables.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/sqlite/deliverables.ts)。
- **优先复用方式**：优先保留 work-product.mjs 的 productsFromFields、safeWebUrl、类型识别与去重代码；本项目提供现有产物/Ref 数据适配，CLI/MCP 输出直接链接和精确身份，不等待 My Work。
- **接入位置**：原 task_state/artifact 与 workscene；拟增交付物投影适配；host.py/cli.py 提供有界列表和详情；Bridge STATE/INDEX 链接；对话可直接引用返回结果。
- **依赖/前置**：CM01；现有 Store/宿主来源回执；相关 CM14 文件路径处理；无 CM08、CM09 页面、CM11 或 CM21 依赖。
- **完成标准**：从当前任务直接列出报告/图片/PR 等产物并得到可打开的路径或链接；绑定 task/source/revision；变更/缺失说明清楚；不用启动完整页面；链接不等于已发布证据。
- **适配边界**：不照搬 Stop 扫描任意目录、自动复制到个人 deliverables bucket；不因发现链接而发送、上传或打开外部资源。

<a id="cm11"></a>

### CM11｜人工已查看标记与版本变化后的重新待查看

- [ ] **P2；新增个人 UI 状态。** 挂接：与 E03 完成事实分离。
- **自用效果**：区分“助手说做完了”和“我已经看过这个结果”，减少漏看。
- **已有能力与缺口**：现有完成和 handoff 回执不是人工 UI review；不改变原任务验收状态。
- **上游实现**：[src/services/work-view/review-store.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/work-view/review-store.ts)；[src/services/worker/http/routes/WorkViewRoutes.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/worker/http/routes/WorkViewRoutes.ts)。
- **优先复用方式**：在完整界面确有使用需求后保留 LocalReviewStore 状态机和 review 交互，让本项目提供版本标识与存储接口；采用现有 task/revision，不为了小功能重写界面。
- **接入位置**：拟增 personal_view 中的用户视图状态表/API；写入原 R 数据库独立 user_view_metadata，不写 task completion。
- **依赖/前置**：后续 CM08/CM10；仅用户显式操作；不作为 CM10 产物入口前置。
- **完成标准**：已查看仅对所选版本有效；结果或必需要求变化后重新显示待查看；存储失败显示未能保存；模型不能替用户点击已查看。
- **适配边界**：不是上线、执行、合并或权限批准；不把上游 content fingerprint 展示为理解证明。

<a id="cm12"></a>

### CM12｜无模型时仍能显式记事、看来源与生成简报

- [x] **P0；扩展原任务。** 挂接：G06；复用现有 record / text recall / deterministic brief。
- **自用效果**：模型渠道或嵌入暂时不可用时，记录重要纠正和继续任务仍能进行。
- **已有能力与缺口**：M 已有来源记录与 text 路径，R 简报确定性；缺统一入口与明确的待归纳提示。
- **上游实现**：[src/services/memory/save-memory.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/memory/save-memory.ts)；[src/services/worker/http/routes/MemoryIngestRoutes.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/worker/http/routes/MemoryIngestRoutes.ts)；[src/shared/progressive-search.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/shared/progressive-search.ts)。
- **优先复用方式**：先复用 save-memory 显式输入和返回结构，让本项目原 M 接口负责权威写入；无模型简报仍调已完成的确定性路径；只补连接，不加上游 observer。
- **接入位置**：integrations/engineering_memory/memory_runtime.py、memory_integration.py；原 memory_cli/MCP；Rail layered.py 与统一 CLI。
- **依赖/前置**：CM01、CM02 与已有记录/text recall/brief；无完整界面或额外客户端依赖。
- **完成标准**：关闭模型能力时能保存显式用户事实并原文回读；查询给出 text 模式与覆盖限制；模型失败不把成功记录改成失败，也不谎称已语义归纳。
- **适配边界**：重复正文不能冒充同一来源事件；保持现有 source-event 身份，不移植单纯内容哈希去重。

- **第一阶段证据**：见 `openspec/changes/claude-mem-self-use-ergonomics/verification.md`；仅勾选本条首批契约，不推断上游整套系统已集成。

<a id="cm13"></a>

### CM13｜明确范围的备份、恢复与可重建索引清单

- [ ] **P1；扩展原任务。** 挂接：H06/H07；复用原生命周期。
- **自用效果**：升级或换目录前有可恢复的数据包，恢复失败知道回到哪里。
- **已有能力与缺口**：客户端配置有备份，Bridge 有 checkpoint 生命周期，R/M 各有原文与派生索引；缺一份整体资产与恢复次序入口。
- **上游实现**：[src/services/backup/BackupManager.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/backup/BackupManager.ts)；[src/services/backup/restore-swap.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/backup/restore-swap.ts)；[src/services/backup/snapshot-files.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/backup/snapshot-files.ts)；[src/services/backup/sqlite-file-job.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/backup/sqlite-file-job.ts)。
- **优先复用方式**：优先保留快照发布、single-flight、恢复阶段和报错流程的上游代码，适配本项目资产清单与原有备份 API；仅数据库后端操作必须不同时替换对应调用，不默认全模块翻译成 Python。
- **接入位置**：拟增 contextrail/maintenance.py；原 integrations/engineering_memory/deploy.py；Bridge checkpoint 保留原 owner。
- **依赖/前置**：CM03；P0 只需复用已有受影响配置的备份/回滚；完整数据备份恢复扩展在首批核心体验之后。
- **完成标准**：导出说明覆盖与缺失；恢复拒绝活动写入并保留前状态；恢复后原 task/version/来源可读；索引可明确待重建；跨库不冒称原子快照。
- **适配边界**：不照搬只按数量删快照覆盖 Bridge 保护；不备份凭据或活跃租约作为可复用授权；不做自动跨机同步。

<a id="cm14"></a>

### CM14｜Windows 的命令、路径和重命名失败处理

- [ ] **P0；扩展原任务。** 挂接：H08；复用 process_identity/native_clients。
- **自用效果**：目录有中文或空格、Python 环境不同、文件被占用时，行为可解释且可恢复。
- **已有能力与缺口**：已有 argv、绝对路径、UTF-8 与进程身份机制；仍需覆盖配置迁移和新文件相关功能。
- **上游实现**：[src/services/integrations/CodexCliInstaller.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/integrations/CodexCliInstaller.ts)；[src/cli/adapters/codex-file-context.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/cli/adapters/codex-file-context.ts)；[src/services/backup/restore-swap.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/backup/restore-swap.ts)。
- **优先复用方式**：直接复用所选上游模块的 argv/spawn/路径与有界重命名重试代码，向它们提供本机解释器和目录配置；只补明确的 Windows/PowerShell 差异。
- **接入位置**：native_clients.py、project.py、process_identity.py 与未来 maintenance.py；M 现有 control.ps1 接口。
- **依赖/前置**：CM01；与 CM03/CM05/CM06/CM10 的实际路径操作随批接入，不要求先完成 CM13 或全环境治理。
- **完成标准**：本批触及的中文、空格、junction/UNC 或文件占用场景有明确处理/恢复材料；不猜工作区；参数不经字符串拼 shell；不要求先做无关平台适配或环境扫描。
- **适配边界**：不自动改 PATH、下载运行时或执行 doctor 的环境探针；上游 Bash 参数解析不能直接用于 PowerShell。

<a id="cm15"></a>

### CM15｜多语言符号大纲与按符号展开

- [ ] **P2；扩展结构读取。** 挂接：已有 chunking.py / C05，补语言与交互。
- **自用效果**：看大源码文件时先取结构，再读目标函数，减少无关原文。
- **已有能力与缺口**：StructuralChunker 已支持 Python 顶层 AST、Markdown 标题和段落；不用重写 Python 这一层。
- **上游实现**：[src/services/smart-file-read/parser.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/smart-file-read/parser.ts)；[src/services/smart-file-read/search.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/smart-file-read/search.ts)；[src/services/smart-file-read/language-map.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/smart-file-read/language-map.ts)；[src/services/smart-file-read/workspace-path.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/smart-file-read/workspace-path.ts)。
- **优先复用方式**：优先完整保留上游 TypeScript parser/search/QUERIES 与符号展开模块，为其输出接 Rail Ref/revision；已有 Python AST 不再重写，多语言模块按实际语言需要采用。
- **接入位置**：chunking.py、hit_windows.py、directory_sync.py；拟增 outline/unfold 只读工具与可选解析适配。
- **依赖/前置**：首批体验完成后按真实源码阅读需求选择；解析运行时/grammar 是此条单独依赖，不阻塞首批。
- **完成标准**：同名嵌套符号可精确选择；展开绑定原 revision/span；文件变化拒绝旧范围；解析器缺失仍可普通原文读取；默认预算使用真实字节。
- **适配边界**：不复制自动下载/编译树形语法、整套几十语言依赖、foldedTokenEstimate 字符估算或 whole-file deny。

<a id="cm16"></a>

### CM16｜显式项目别名与迁移目录后的历史查找

- [ ] **P2；补身份迁移。** 挂接：A02 已完成，补 aliases。
- **自用效果**：同一仓库换了路径或旧项目命名，历史还能找到且不会串任务。
- **已有能力与缺口**：project.repository_identity 已区分 repo/worktree/task；现有 QueryBinding 明确来源。
- **上游实现**：[src/services/sqlite/project-read-keys.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/sqlite/project-read-keys.ts)；[src/services/work-view/work-state-reduce.mjs](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/work-view/work-state-reduce.mjs)。
- **优先复用方式**：优先复用 projectReadKeys/read-aliases 的查询模块，本项目提供可信别名映射和现有 scope 过滤；保留别名来源。
- **接入位置**：project.py、query_sources.py:QueryBinding、federation.py；原 M scope alias 配置。
- **依赖/前置**：CM04；只读显式别名；不推断目录同名等价。
- **完成标准**：旧路径可查且展示原来源；同仓库不同 worktree 仍分开；别名冲突明确拒绝；原 task 与 snapshot 身份保持。
- **适配边界**：上游按项目键合并的规则不能用于扩大授权、选择最新任务或转移租约。

<a id="cm17"></a>

### CM17｜宿主原生记忆的定向只读查找与显式导入

- [ ] **P2；扩展原任务。** 挂接：G08；E07 的会话历史继续原归属。
- **自用效果**：已有 CLAUDE/Codex 记忆文件能找到，选择后再纳入 main，减少手工复制。
- **已有能力与缺口**：M 已有 import-document；R 已有目录导入与来源排除；不是再建采集器。
- **上游实现**：[src/cli/handlers/memory-search.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/cli/handlers/memory-search.ts)；[src/services/memory/save-memory.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/memory/save-memory.ts)。
- **优先复用方式**：复用 memory-search 的路径识别与查询接口，让本项目提供受限原生目录和现有导入服务；不重建监听器或导入引擎。
- **接入位置**：integrations/engineering_memory/history_import.py；directory_memory.py；统一搜索导航。
- **依赖/前置**：CM07、CM12；可信来源目录清单，原 F03 学习来源过滤。
- **完成标准**：读与导入分别确认结果；路径无越界；导入保留角色/原文/事件 ID；派生恢复文件不再触发新学习。
- **适配边界**：不自动扫描整个 home，不导入隐藏推理或凭据，不把文件名当系统指令权限。

<a id="cm18"></a>

### CM18｜有出处的偏好视图与规则差异草稿

- [ ] **P2；扩展原任务。** 挂接：G01/I07/G05。
- **自用效果**：能看见长期偏好来自哪次纠正，选择接受、关闭或替换整理建议。
- **已有能力与缺口**：main 是偏好权威，M 已有事实历史与 superseded 处理；不另建 My Rules 权威。
- **上游实现**：[src/services/work-view/my-rules.mjs](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/work-view/my-rules.mjs)；[src/services/work-view/request-groups.mjs](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/work-view/request-groups.mjs)；[src/services/worker/http/routes/WorkViewRoutes.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/worker/http/routes/WorkViewRoutes.ts)。
- **优先复用方式**：后续采用原 my-rules 视图与规则状态代码，本项目适配 main 事实/作用域和差异草稿接口；现有模块能保留时不重写。
- **接入位置**：M memory_runtime.py 的当前事实/关系接口；拟增个人视图 preference 投影；Bridge requirements 不被偏好覆盖。
- **依赖/前置**：CM08、CM12；原用户显式偏好与来源；现有归纳预算。
- **完成标准**：每条偏好能回读来源、看作用域与替代关系；否决建议不重复弹同版本；当前任务纠正优先；应用差异作为单独显式操作。
- **适配边界**：不照搬自动更新 MY-RULES.md 并导入 CLAUDE.md；助手推断不能自动升格用户指令。

<a id="cm19"></a>

### CM19｜重复确认的经验参与候选排序

- [ ] **P2；挂接原任务。** 挂接：G07；与 I05 区分。
- **自用效果**：老但反复被真实工作确认的经验，不总被最近的一次记录挤掉。
- **已有能力与缺口**：M 已有 proof_count 和来源去重；先复用现有证据，不新增向量库。
- **上游实现**：[src/services/reinforcement/strength.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/reinforcement/strength.ts)；[src/services/reinforcement/rank.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/reinforcement/rank.ts)；[src/services/reinforcement/persist.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/reinforcement/persist.ts)。
- **优先复用方式**：保留 strength/rank 的纯函数原实现，通过本项目适配器提供真实来源确认记录；只选所需函数，实验默认关闭。
- **接入位置**：integrations/engineering_memory/memory_runtime.py 候选选择与原事实 proof/evidence；用既有评测框架。
- **依赖/前置**：CM01；CM24 先冻结该项的具体校准场景；独立于首批核心功能。
- **完成标准**：重放同一来源不加权；较老的真实再确认可影响可选候选；当前冲突/撤销事实仍排除；必需要求和最新纠正不被排序替换。
- **适配边界**：上游 UTC 天桶与默认 alpha 是算法选择，不代表对中文任务更好；不把读取/自动注入本身算再次验证。

<a id="cm20"></a>

### CM20｜稳定简报缓存与动态时间标签分离

- [ ] **P1；补现有缓存。** 挂接：C08 已完成；只补渲染路径。
- **自用效果**：频繁 Hook 不重复构造相同简报，又不会把“几分钟前”缓存成错误时间。
- **已有能力与缺口**：federation.QueryState 已按来源版本缓存；现有 packet/current 检查继续保留。
- **上游实现**：[src/shared/context-cache.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/shared/context-cache.ts)；[src/shared/host-hook-limits.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/shared/host-hook-limits.ts)。
- **优先复用方式**：实际出现重复装配开销时，先复用 context-cache 的稳定正文/动态时间函数和 host deadline 计算，由本项目提供精确 task/source version 键；不替换已有查询缓存。
- **接入位置**：native_clients.py、layered.py、local_status.py；可选现有事件表缓存元数据。
- **依赖/前置**：CM05/CM06 已可用且有实际重复成本证据后处理；不作为它们首次交付的前置。
- **完成标准**：source/task/revision/scope/配置变化失效；缓存命中仍核对必需来源当前性；动态时间来自明确时钟；超时返回可说明的来源缺口。
- **适配边界**：不照搬 24h TTL 作为当前性保证；缓存不能长期复活已失效要求；没有最终请求观测不报告省 token。

<a id="cm21"></a>

### CM21｜完整页面的断线、陈旧数据与来源提示（后置）

- [ ] **P2；新增展示适配。** 挂接：H01/H02 的界面补强。
- **自用效果**：页面连着不代表任务仍在跑；后台暂时不可读也不该让旧任务消失。
- **已有能力与缺口**：local_status 已说明 process_liveness/model_consumption unknown；新界面需要保留这一边界。
- **上游实现**：[src/ui/my-work/src/live/connection-status.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/ui/my-work/src/live/connection-status.ts)；[src/services/worker/http/routes/WorkViewRoutes.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/worker/http/routes/WorkViewRoutes.ts)。
- **优先复用方式**：随完整页面直接复用 getConnectionStatus 和其 UI 状态映射，适配本项目接收时间与状态来源；当前 CLI/MCP 的故障说明归 CM02，不能反向依赖页面。
- **接入位置**：后续复用的 My Work 页面/连接状态模块；现有 local_status/来源接口提供数据。当前聊天、CLI/MCP 的故障说明由 CM02 承担。
- **依赖/前置**：后续 CM08；当前故障解释/交付物入口不依赖此项。
- **完成标准**：transport/source/freshness 三种状态独立；重连保留旧记录并标时效；时钟偏差有说明；状态刷新不产生模型调用或新的采集事件。
- **适配边界**：不执行 health/startup 探针来填状态；页面在线不推断 Agent 运行。

<a id="cm22"></a>

### CM22｜常用任务、查询和产物的手动收藏与分组

- [ ] **P2；扩展原任务。** 挂接：I04；复用已有项目/任务身份。
- **自用效果**：常回看的任务和搜索不用重复输入长参数。
- **已有能力与缺口**：已有精确任务和恢复候选；缺个人导航状态。
- **上游实现**：[src/services/work-view/my-focus.mjs](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/work-view/my-focus.mjs)；[src/services/work-view/owner-goals.mjs](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/work-view/owner-goals.mjs)。
- **优先复用方式**：后续复用 my-focus/owner-goals 中稳定分组和 placement 代码，本项目提供任务身份和 UI 元数据存储；暂不搬模型聚类。
- **接入位置**：personal_view 用户视图元数据；cli.py 可复制精确 argv；原 query/recover 入口。
- **依赖/前置**：CM08、CM10；收藏保存 task/snapshot/version 或明确 current 模式。
- **完成标准**：重命名保留链接；失效引用展示原因；收藏不会自动领取任务；查询收藏不缓存成已验证结果。
- **适配边界**：不新增个人目标规划 Agent，也不把界面分组当工作区授权。

<a id="cm23"></a>

### CM23｜生成可读的目录历史卡片

- [ ] **按需；可选导出。** 挂接：H02/I07；复用 F03 派生来源过滤。
- **自用效果**：需要离线浏览某目录改动缘由时，能有一份可更新的历史说明。
- **已有能力与缺口**：已有 timeline/STATE/INDEX 导出；不用另建 folder memory 存储。
- **上游实现**：[src/utils/claude-md-utils.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/utils/claude-md-utils.ts)。
- **优先复用方式**：需要离线卡片时直接复用 tagged-content 更新与临时文件发布模块，本项目提供显式输出位置、来源头和历史材料；不默认翻译整套导出工具。
- **接入位置**：现有 timeline/workscene 导出；拟增 folder-card CLI；source origin 标为 history_only。
- **依赖/前置**：CM06、CM09；仅显式选择目录/输出位置。
- **完成标准**：人工块保留；每段带来源与生成版本；重放卡片不学习；过期卡片显示历史性质；拒绝覆盖非受管文件。
- **适配边界**：不自动写 CLAUDE.md/AGENTS.md，不把历史生成文本提升为宿主指令。

<a id="cm24"></a>

### CM24｜用真实自用任务评估这批增量的收益

- [ ] **P1；挂接原任务。** 挂接：I08；复用现有 evaluation/accounting。
- **自用效果**：确认这些功能是否减少找资料、重复读取和漏要求，而不是只增加界面。
- **已有能力与缺口**：已有 UsageLedger、真实来源计量和评测脚手架；目前缺同任务最终请求与账单对照。
- **上游实现**：[src/shared/progressive-search.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/shared/progressive-search.ts)；[src/services/work-view/review-store.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/services/work-view/review-store.ts)；[src/ui/my-work/src/live/connection-status.ts](https://github.com/thedotmack/claude-mem/blob/b80b29bc49d08a71312f44b8feacb2e7585edff4/src/ui/my-work/src/live/connection-status.ts)。
- **优先复用方式**：直接利用所选上游代码的 trace/回执字段与现有 accounting 做当前场景记录；每个阶段只验证该阶段用户行为，不先搭全套对照实验。
- **接入位置**：contextrail/accounting.py、evaluation/ 与个人操作记录；不新增默认监听器。
- **依赖/前置**：随本批 P0 实施冻结相应当前行为场景；广泛基准、费用或额外模型实验单独按需。
- **完成标准**：用“读某文件找上次原因、压缩后保留末尾纠正、找到交付物、故障下保存记录”等场景记录操作次数、原文回读和要求遗漏；费用只在有成对最终请求与同单位用量时报告。
- **适配边界**：不自动调用付费模型、不运行旧套件或泛化性能结论；经验排序等实验项需单独明确收益目标。

## 分阶段安排

| 阶段 | 范围 | 结果与边界 |
|---|---|---|
| A：稳定性与故障解释 | CM01/CM02/CM03/CM12；CM14 随触及路径；CM04 只读取配置来源部分 | 不重复采集、不覆盖未知配置；原入口能说明原因；模型不可用仍能保存记录；复用现有备份 |
| B：压缩后继续 | CM05 | 支持事件中补回当前要求和纠正；一次处理，失败可见，未送达保持未知 |
| C：文件旁的历史 | CM06；CM07/CM09 仅当前工具导航所需部分 | 读改文件时有短历史和精确来源，同版本不重复；不等完整详情页面 |
| D：交付物入口 | CM10 | 当前聊天、CLI/MCP 能直接找到并打开结果；不依赖 CM08/CM11/CM21 |
| E：后续按需 | CM08/CM11/CM21 完整界面；其他 P1/P2/按需扩展；更多客户端 | 只有实际操作成本或第三客户端使用需求出现时再选；优先保留上游页面/适配器 |

阶段 A 已实施；CM04 仅只读来源、CM14 仅本批 Windows 配置路径完成，完整条目不勾选。CM24 仅记录本批行为证据，后续场景不勾选。先完成实际用户行为再决定是否增加页面或服务。首批只覆盖 Codex/Claude，其他客户端不做通用适配框架、不先安装，也不列首批验收条件。

未来实现按当前开发契约完成本批最小行为 TDD 和阶段文档；不从本文取得旧套件、泛化基准、探针或付费调用的额外授权。CM24 随各阶段记录“是否少问一次、能否找到来源、失败后是否保留数据、是否能直接打开产物”，不先做大规模 benchmark。本阶段仅执行新增行为契约与必要构建/schema 检查；具体命令与结果见 verification.md。

## 后置范围的启用条件

- **完整 My Work / 设置编辑 / 人工已查看 / 页面连接提示**：当前入口已经可用，且确有频繁浏览需求；届时直接接上游页面组件和 API，不默认重写。
- **Codex/Claude 之外的客户端**：用户实际使用该客户端，并明确需要同一任务/记忆能力；再选择对应上游 installer/adapter，本批不追求覆盖数量。
- **完整数据备份恢复、多语言大纲、强化排序与额外缓存**：出现对应缺口或性能证据后单独选择，保留原任务编号与来源。
- **常用分组、偏好界面、目录卡片**：按使用频率补，不作为核心自动化的前置。

## 不另做或不直接照搬

| 上游部分 | 决定与原因 |
|---|---|
| Bun worker / SQLite + Chroma 完整数据层 | 现有数据权威继续保留；允许为选定模块使用原语言运行时/小接口服务，整套 worker/Chroma 不作为默认前置 |
| work_state_write 强制替换宿主 TODO | 现有 WorkScene/Bridge 要求/验收权威保留，只借展示投影 |
| whole-file Read deny 与自动路由 smart_* | 目录、语言和解析器能力并非总齐备；先做可选 outline，不拦截正常读取 |
| 自动写 MY-RULES.md、CLAUDE.md/AGENTS.md | 改成草稿或独立历史卡；历史不自动变成高优先级规则 |
| My Work 模型分组/进度百分比/自动刷新归纳 | 完整 My Work 已后置；启用时接真实 task/version/status，未知不补百分比；不加默认 judge 循环 |
| TeamBrain、云同步、账号试用与付款、Telegram/Notion 通知 | 当前单机自用无需这些依赖与外部写入 |
| 模型/provider 自动切换与额度建议 | 原模型、预算与登录保持既有 owner；状态展示与切换分开 |
| 全量截图/媒体推理、重型 corpus/ragtime | 增加存储、模型和检索链，当前未发现明确自用缺口；不列首批任务 |
| zstd transcript 扫描器 | 上游有边界/完整帧处理，但当前未核对本机来源格式需要；作为 E07 格式扩展线索，不新建解压器 TODO |
| 英文 terms/rank、字符估 token、固定 10x 节省说法 | 不覆盖现有中文/多语召回与计量规则；收益待本机同任务证据 |
| 自动清理、doctor、安装器一键执行 | 当前只读实现，不运行；恢复与维护须沿用原资产范围与保护 |
| 上游到处 fail-open | 可选历史不可用可继续；当前 required 资料失效仍走原阻断/报缺口规则 |

## 优先直接复用的模块与本项目要改的接口

| 上游现成代码 | 优先保留 | 本项目承担的适配 | 主要局部依赖 |
|---|---|---|---|
| owned-file-install.ts:replaceOwnedFiles | 原阶段替换/逆序回滚主体 | 自有文件和配置片段清单、日志、安装回执 | Node 文件 API；TypeScript 构建按原模块需要 |
| post-compact.ts＋session-marker-files.ts＋codex-compaction-gate.ts | 原 handler、mark/take 主流程 | 数据目录、精确任务绑定、现有 packet 与准备/返回记录 | 文件 API、paths/logger facade；实际宿主事件 |
| file-context.ts＋file-context-dedupe.ts＋codex-file-context.ts | handler、路径形式、格式器和原子 claim | 实现 by-file 查询、原库 gate 接口与 source revision 映射；剥离读拦截 | 原持久化实现用 bun:sqlite；选用时记录运行时或只替换存储调用 |
| observer-health.ts | 分类、时长和故障说明函数 | 把原 jobs/control/prepared 状态变成函数输入；现有入口返回 | 选定函数的最小导入闭包；不要求新账本 |
| work-product.mjs:productsFromFields/safeWebUrl 等 | 产物字段、类型识别与去重函数 | 传入现有声明/Ref，提供聊天/CLI/MCP 链接与回读 | Node path/fs 中实际使用部分；不必 React/HTTP worker |
| progressive-search.ts | backend 协议、trace 与输出组织 | 实现现有 query/timeline/get 转换 | 既有搜索后端；原类型与格式依赖 |
| React My Work/SessionDetailPage/Settings 组件 | 后续保留组件与状态映射 | 本项目后端匹配 API、来源身份、实际状态 | React/构建依赖届时选最小闭包；现阶段后置 |
| LocalReviewStore / connection-status | 后续保留小状态机和纯函数 | 原 R 独立 UI 元数据、task/revision 与接收时间 | 完整界面实际启用后处理 |
| parser.ts/search.ts/QUERIES | 后续优先保留多语言解析主体 | 输出转成 Rail Ref/revision，复用已有 Python AST | 只选实际语言与 parser 依赖，不阻塞首批 |
| reinforcement / context-cache | 保留选中原函数 | 真实来源确认和精确版本键 | 有具体收益目标后再接 |

每次实现需给出“实际保留哪些源文件/函数、我方增加哪些接口、改了上游哪些行、为何需要这些补丁”。不能把只有算法说明或新写的等价 Python 模块称为直接复用。研究副本目前不是可运行依赖闭包，集成/构建完成与下载分开报告。

## 许可与交付记录

上游固定 commit 的主 LICENSE 和 NOTICE 为 Apache-2.0。后续实际复制/移植代码时保留原版权和许可，登记本地改动；其第三方依赖、grammar、字体和图片仍须分别核对，主仓库许可不能代替各依赖许可。这里是源码来源登记，不是法律结论。

研究原件位于本聊天 work/claude-mem/b80b29bc49d08a71312f44b8feacb2e7585edff4/；来源清单见随报告交付的 claude-mem-source-index.json。仓库内 TODO 为 docs/claude-mem-self-use-todo.md；规划为 openspec/changes/claude-mem-self-use-ergonomics/。本轮没有移植运行时代码、升级安装、改客户端配置、提交、推送或调用模型。

完成标准均是未来实现应观察的行为，不是本次已经通过的测试。用户已确认这次优先顺序和直接复用方向，未逐项接受后置功能、运行时选择或授权本轮开始实施。

