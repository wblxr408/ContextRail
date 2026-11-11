# 自用模块交付

按 TODO 持续实施。完成一大阶段后集中验收，再提交上传该阶段。权威清单为 [docs/self-use-integration-todo.md](docs/self-use-integration-todo.md)。当前完成 70/72。

## 第二十模块 I08

Windows 本机使用真实自用任务 revision 3 和实际三源查询运行日志。独立确认的正确下一步包含用户末尾追加的模块上传要求。以四条必读要求、指定日志字节窗口和任务隔离为验收条件。

| 场景 | 实际结果 |
| --- | --- |
| 大日志 | 原日志 141,911 字节。完整材料交付 246,517 字节，本地耗时 57.181 ms。任务包和冷页响应合计 18,997 字节，耗时 73.731 ms。 |
| 中文末尾纠正 | 两条路径均保留已确认的下一步与四条必读要求。按需路径另列六个未内联的证据引用。 |
| 两任务并存 | 同一私有库保存持续实现任务和本聊天的数学可视化可行性问题。两份快照分别回读，交叉读取遭拒，第二任务原文目标一致。 |
| 客户端中断 | 原生 Codex 可见历史报告最后一次请求后无可见最终答复，并有一个结果未知的调用。原操作耗时保留未知。 |
| 冷页 | 从未内联的真实日志通过一个精确范围取回 16 字节，与指定原文一致。 |
| 模型不可用 | 实际受管 Claude 收到 429，170.86 秒结束等待，未观察到任务包调用。该次验收的同一快照可离线读回，目标一致，本地副本及读取耗时 65.938 ms。 |

实际取用的字节和本地单次耗时各自报告。模型实际下一步、供应商用量和总体易用性保留未知。其余已采纳模块的可观察行为按下方交付记录及链接复核。当前新增行为用例和 wheel 构建通过。真实日志、原生回执和数据库副本只在本机。见 [本地用法](SELF_USE.md#用实际任务校准读取收益)。

交付分支 `codex/real-task-calibration`。

## 第十九模块 I03

选定的 Bridge 原始账本及证据可携带到另一工作区。显式 Rail Ref 在接收端保留来源映射，知识链接和工作文件依赖可分别回读和检查。导入以接收端租约发布，包含相同包重送、变更证据拒绝和凭据材料拒绝。

Windows 上两项集中用例和 wheel 构建通过。实际自用任务 revision 3 在新中文目录中恢复四条必读要求和最新用户纠正，依赖 `contextrail/bridge.py` 从源端的 present 变为接收端的 missing。验收使用同一台 Windows 主机的隔离目录。见 [跨目录恢复](BRIDGE_USE.md#带到另一处工作区)。

交付分支 `codex/portable-task-bundle`。

## 第十八模块 I01 / H08 实际验收

受管入口启动选定的 Codex 或 Claude Code，将精确快照和用户原文送入原生 CLI。复用现有只读 MCP、Windows Job、源会话读取与原生配置。两个阶段用例验证两端回读同一快照及 CRLF 冷页、原生退出码和会话身份，并验证无关快照在启动前被拒绝。wheel 构建通过。

在 Windows 本机中文临时目录运行 `examples/native_client_acceptance.py --managed`。Codex 调用 `context.packet` 与 `context.get`，原文和最终 JSON 均一致，退出 0。Claude Code 启动事件返回自己的原生 ID，报告当前任务 MCP 已连接并列出 34 个 Rail 工具。模型接口记录了 10 次 429 重试，170 秒内未调用 MCP，启动器返回 124。回执保留同一快照与未绑定的源历史状态。两项仍保留开放。见 [受管启动与回执](NATIVE_CLIENTS.md#通过指定客户端继续同一工作块)。

交付分支 `codex/native-managed-continuation`。

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

## 第四模块：D03 / D04 / D06

带来源 span/context 的字面证据贯通摘要搜索/读取与 M 输入；原提交入口执行覆盖与冲突门，拒绝草稿可读。知识页保存不可变版本，来源变化/刷新失败时返回 stale 和历史入口。当前17个消费者均有对应GREEN记录；集中验收及具体缺口修复范围见[阶段验收](openspec/changes/grounded-summary-refresh/verification.md)。

已上传模块：[b88bb86c](https://github.com/wblxr408/ContextRail/commit/b88bb86c7c7d3785de2d9b381019d65213aeeee4)，origin/main；GitHub精确回读确认。

## 第五模块：E02 / E03 / B08

工作现场与原生事件/宿主结果接线，预绑定完成证据、状态声明/推断和原actions在途恢复已实现。显式句柄恢复与树取消保留未知，不自动重跑；操作日志以有界导航进入任务上下文。当前24个消费者有对应GREEN，原M部署与实际Git/环境、STATE及MCP链路见[阶段验收](openspec/changes/recoverable-workscene/verification.md)。

已上传模块：[d7143d47](https://github.com/wblxr408/ContextRail/commit/d7143d476b868e5f74d8c54f7e232b02936235ad)，origin/main；GitHub精确回读确认。

## 第六模块 E04 / E07

错误处理与验证记录按来源绑定，相邻成功保留为候选。补读精确 Codex 或 Claude 会话，返回缺口、未知调用及处理版原文 Ref，并可导出 Bridge 恢复材料。Windows 实际子进程、双客户端日志及 18 项阶段用例通过。详见 [阶段验收](openspec/changes/causal-recovery-history/tasks.md)。

交付分支 `codex/causal-recovery-history`。

## 第七模块 H04 / H05

统一 CLI、Bridge 和原生接线的项目预算，支持个人默认值、项目覆盖和单次覆盖。升级在原位置更新受管 Hook，移除按回执保留原配置和记忆流程。Windows 上 11 项消费者与受影响回归通过，两端使用真实 MCP stdio 读取任务包和冷页，wheel 构建通过。验收使用临时工作区及符合既有协议的记忆 Hook。见 [项目配置](SELF_USE.md#项目配置与初始化)和[客户端接线](NATIVE_CLIENTS.md#任意自用任务的准备与应用)。

交付分支 `codex/project-config-ownership`。

## 第八模块 H06 / H07

按角色在线备份 Rail、配置、已提交代码、Markdown 与可选记忆队列。恢复输出单独的审阅目录，保留快照、必读引用、动作终态与待核对动作。Rail 索引损坏时从原文重建证据 FTS 和时间线；缺失权威表或非法动作状态会拒绝恢复。Windows 本机 6 项消费者与 wheel 构建通过，队列验收使用有实际 WAL 和未提交写入的临时 SQLite 库。见 [恢复操作](SELF_USE.md#本地备份与故障恢复)。

交付分支 `codex/local-maintenance-recovery`。

## 第九模块 H08

Windows 本机 5 项集中验收和 wheel 构建通过。PowerShell 通过中文 junction 安装配置并写入同一任务，Codex 与 Claude 配置的 Hook 和真实 MCP stdio 完成读回。批处理保留特殊字符，诊断与协议输出分离，Windows Job 清理超时、阻塞管道和遗留子进程。

原生 Codex 在中文目录中完成任务包与冷页精确回读，正常退出。Claude 原生 Hook 有实际回执，模型接口返回 429，原生 MCP 回读仍待验证。H08 在 TODO 中保留开放。见 [Windows 本地调用](NATIVE_CLIENTS.md#windows-本地调用)。

交付分支 `codex/windows-native-transport`。

## 第十模块 C04 / C07

检索保留路径、中文短词和标识符的原文 Ref，前缀与拼写候选单独展示。既有索引可继续查询，显式重建后常驻查询重新读取。网页缓存按任务、可信来源及 URL 存储有版本的观察，明确过期、刷新和 HTTP 重验证。Windows 本地 10 项集中验收和 wheel 构建通过，HTTP 链路使用实际回环服务器。见 [查询与网页缓存用法](QUERY_USE.md)。

交付分支 `codex/retrieval-web-evidence`。

## 第十一模块 G06 / I04

离线简报从精确快照保留需求、事件、动作和原文 Ref，原学习队列可只读翻页。书签固定工作树内任务与快照，可保存搜索并在 PowerShell 补全。Windows 本地 8 项集中验收和 wheel 构建通过，包含原记忆运行时显式记事、Bridge 来源校验及中文 Tab 补全。另只读验证了实际工作区的原记忆队列。见 [离线简报与任务书签](BRIDGE_USE.md#离线简报与任务书签)。

交付分支 `codex/offline-task-navigation`。

## 第十二模块 G02 / G03

事实收录时间和事件时间分开，知识页按成功发布窗口读取不可变历史。原 Markdown 的 fixes、contradicts、causes 在查询中保留两侧出处和范围。Windows 本地 8 项集中验收及 wheel 构建通过，含独立 vault 的真实回环 RPC。通过只读连接从原库取回一个真实归档知识页，385 字节、4 条来源。实际共享服务部署沿用现有更新流程。见 [记忆版本和关系](GROUNDED_SUMMARY_USE.md#按收录时间回看)。

交付分支 `codex/temporal-memory-relations`。

## 第十三模块 G04 / G05

按指定工作区、会话和时间归纳重复步骤、相邻两步操作与重复失败，返回调用和结果的原事件引用。否决写入原记忆库，保留理由和证据版本。新证据或用户显式重看会给出重新显示的原因。

Windows 本地 4 项集中验收和 wheel 构建通过。临时原生事件库覆盖来源隔离、桌面代码工具的明确退出状态、候选分页、幂等否决和证据变化。只读抽查原库四个会话的近期捕获及三个时间窗口，识别到有明确结果的操作，窗口内未发现满足跨会话重复条件的候选。见 [跨会话经验审阅](QUERY_USE.md#跨会话经验审阅)。

交付分支 `codex/cross-session-experience`。

## 第十四模块 G07

从 H03 实际回读的字节窗口计算热度，支持可选目录排序和显式预取。保留既有优先级，依赖组整体装载。整理建议保护必读项、现行任务来源、依赖组和 Bridge 权威原件。

Windows 本地 6 项集中验收和 wheel 构建通过。实际 MCP stdio 回读在断开后驱动 CLI 同预算预取。用例覆盖未送达的批量结果、精确 revision 与字节窗口、低热度建议、必读保护和共享依赖组。独立 Basic Memory 读操作没有 Rail 回执，仍在此观测范围之外。见 [按回读热度预取](ACCOUNTING_USE.md#按实际回读热度预取可选材料)。

交付分支 `codex/read-heat-prefetch`。

## 第十五模块 G08

PDF、DOCX、XLSX 与 PPTX 通过原来源账本及学习队列显式导入。回执保留解析损失，统一查询按具体坐标打开已存档版本。原生记忆从可信绑定只读查询，文件变化后旧 Ref 要求重新查询。

Windows 本机 4 项集中验收和 wheel 构建通过。旧记忆入口在隔离部署中验证了重复导入和新版回读。新增解析器为可选依赖，PDF 范围为文字层。见 [资料导入与回读](QUERY_USE.md#导入本地资料并回读原生记忆)。

交付分支 `codex/document-native-memory`。

## 第十六模块 I06 / I07

原记忆服务提供近重复候选、原件差异和版本回读。确认后折叠展示并可撤销，数值、用户纠正和声明关系有精确出处。项目规则差异稿引用选定经验版本，展示疑似冲突并生成反向补丁。两种审阅结果保留为派生数据。

Windows 本机 4 项集中验收和 wheel 构建通过。通过实际回环 RPC 核对笔记分组与版本变化，在副本实际应用正向及反向补丁。隔离部署验证折叠决定在重开原库后仍可读并可撤销。实际共享服务接线使用原部署流程。见 [整理与规则草稿](QUERY_USE.md#可逆整理近重复笔记)。

交付分支 `codex/reversible-memory-review`。

## 第十七模块 G01 / I05

用户原话、工作区边界、主题覆盖和撤销贯通个人默认视图与启动简报。统一查询可显式启用自适应检索，词法先行，在同页新增候选时保留有预算的语义结果。

Windows 本机 3 项集中验收和 wheel 构建通过。覆盖实际回环 RPC、原生 Hook、跨项目隔离、撤销幂等、精确路径及错误码、批次上限和模型失败回退。隔离部署验证重开原库后的默认和离线错误码回读。对原共享模型的一篇已知笔记做定点对照，关键词直接命中，同义查询通过语义补充找回。见 [偏好与检索](QUERY_USE.md#在启动简报中使用个人默认偏好)。

交付分支 `codex/personal-memory-retrieval`。
