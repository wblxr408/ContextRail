# A–D 完成审计

2026-11-01，Asia/Shanghai。目标为用户确认的完整 A–D；完整界面、更多客户端和其他后置项维持按需，不计作本轮遗漏或已交付。原 24 CM 候选和旧 72 项不因此全部勾选。

## 逐项依据

| 任务 | 权威实现/交付物 | 观察到的完成依据 |
|---|---|---|
| 1.1 CM01 | `_vendor/claude_mem/provenance.json`、LICENSE/NOTICE、package-data | 原 TS/MJS 实际被适配路径调用；来源固定 b80b29b，不以下载代表完成 |
| 1.2 CM03 | client_config + owned-file-install 原事务 | 第一阶段 7 项行为记录仍适用于未改的事务；本轮新增多事件安装/精确撤销保留用户 Hook |
| 1.3 CM14 本批 | 原 renameWithWindowsRetry；argv/UTF-8；literal AST | 第一阶段真实 Windows locked handle 回滚；本轮中文/空格 PowerShell 路径实际增强，复杂输入跳过 |
| 1.4 CM02/CM04 只读 | local_status 原来源与新 native_assistance 视图 | 原额度/认证/索引用例；本轮 stdout 返回回执可从 status 读取，模型消费仍 unknown |
| 1.5 CM12 | explicit_memory → 原 M.retain | 第一阶段实际 Markdown/SQLite 无模型保存、同 ID 一条、异文冲突、text 回读通过；本轮未重做 M |
| 1.6 CM24 A | 第一阶段 verification + 原备份/restore-plan | 实际来源、处理结果、失败恢复已记录；无整体数据备份扩张 |
| 2.1 CM05 | post-compact、session-marker、gate 原代码 + native_assistance | 官方两端事件契约核对；真实 Hook stdin/stdout 子进程处理 PostCompact/SessionStart/PreToolUse/PostToolUse |
| 2.2 CM05 | 原 Rail DB delivery controls/events + exact Bridge packet | 同 cycle 一次、真实新 cycle 再补、来源滞后/失效/预算失败可解释且可恢复、prepare 后进程损失恢复、主会话边界 |
| 3.1 CM06 | 原 file-context handler/rank/format/path、原 CLAIM SQL；原 M/R timeline | 实际路径历史注入；版本集合映射 gate epoch，原原子 SQL 由原 R 执行，未增加知识库/采集器 |
| 3.2 CM06 | file_history、PowerShell literal adapter | 同版本正文抑制、新来源再补、worktree 外拒绝、歧义命令跳过、可选源失败仍普通读取；无 Read deny |
| 3.3 CM07/09 核心导航 | 原 context.query/timeline/get + history_details/neighborhood | 搜索命中携带可直接调用参数；原三源 refs 保留；详情部分失败逐项显示，不改任务/租约 |
| 4.1 CM10 | 原 work-product.mjs + original artifact/task_state projection | CLI 列表/详情和真实 MCP 工具发现→列表→详情均找回登记结果，无完整界面依赖 |
| 4.2 CM10 | deliverables 源/版本/根目录登记与 view | 同 ID 重放一个效果；不同版本需显式 expected_revision；历史 record_ref 可回读，当前/改变/缺失/链接声明分离 |
| 4.3 CM24 D | 本审计、完整指南、阶段进度、来源与交付记录 | 四类场景的当前行为均有证据；不把未做的长期费用/内存/收益基准补成数字 |

第一阶段的权威记录为同 change 的 `verification.md` 与已推送 `31b04d90`；它是历史功能证据，不声称本轮重跑了旧用例。第一阶段数据权威、保存实现和上游替换/回滚核心没有重写。

## 本轮新增行为契约

仅运行 `tests/test_claude_mem_compact.py`、`test_claude_mem_file_history.py`、`test_claude_mem_deliverables.py` 的本批消费者契约。当前分别 7、5、5 项；以最终交付日志的实际结果为准。

最终 D:/ContextRail 源码合并后执行这 17 项，全部通过、0 跳过（44.126 秒）。其他 native-history/error-history/研究在途改动保留且未暂存/提交。日志为聊天 outputs/A-D-integrated-behavior.txt。wheel 构建成功；安装 wheel 到项目隔离 SDK 1.30 环境后，在无仓库包路径的目录执行实际 PowerShell 文件历史和 MCP 交付物流，2 项通过（5.758 秒），证明分发资产被业务路径消费。未用文件存在性/导入/启动探针代替分发验证。

RED 观察：缺少 context-assistance CLI；新 Hook 事件未合并；prepare 后标记消失导致无法恢复；PreToolUse 未补必需要求；status 无 returned 说明；文件历史无正文；可选源失败无缺口；无产物登记命令；历史登记无 record_state；搜索命中无可用 navigation。随后对应目标转 GREEN。

fixture scope/编码/依赖修正不算功能 RED。真实 stdio 额外暴露旧适配器与 SDK 2 的 API 不兼容，属于诊断出的运行依赖问题，不以初始化异常充当业务验证。修复后同一交付物业务流在当前 SDK 2 与隔离 SDK 1.30 都通过：工具发现、实际源路径、精确来源详情、拒绝 scope override。未运行旧 SDK 的已有套件。

## 可观察结果与适用范围

- 压缩：已绑定主会话下，原 context.compacted 身份对应一个 returned 记录；普通重放不重复恢复正文，新事件可恢复一次。下一支持事件包括 PreToolUse，优先保留当前要求和末尾纠正。
- 文件：同来源版本重复读取得到 0 份新增历史正文；新来源可再次返回。历史附精确详情/邻域参数；即使单个来源不可读也保留其他可用项。
- 产物：一次列表即可取得本任务登记结果和可打开绝对路径，详情可直接回读登记/来源版本；修改和删除各有明确状态，旧登记不被换成当前版本。
- 故障：源码失效、预算不足、传输未确认、可选历史不可用、原库状态不可读不被显示为完成/丢失/已消费；无模型显式记录继续复用原 M 证据。

这些是本批确定性行为结果。未测“真实长期自用少问多少次”、费用、整体输入或内存收益，不从上游宣传数字推导本项目收益。

## 分发、执行与边界

本轮本机隔离工作树接续第一阶段任务分支。cloud exec 先前实际失败，未创建云端任务。源目录同步、最终提交/推送、构建、schema 与安装 wheel 的业务验证见聊天交付记录及输出日志；不以任务勾选代替这些证据。

新 native 辅助需显式 context-assistance 和准确 QueryBinding。当前已打开客户端没有重新信任/重启验收；本文不声称热更新、已改变 trust 或已绑定所有新会话。安装/启用方法见 `CLAUDE_MEM_SELF_USE.md`。这是配置机制的既有授权边界，不增加任意客户端或新模型调用。

外部文件版本只观察文件身份/大小/mtime/ctime，metadata_matches 不是正文版本证明；不复制产物，不做 hash 比较。原文件改变后打开的是当前文件，登记版本和来源仍可精确回读。外部链接未经远端检查，不代表发布成功。

M note 的历史版本/事件锚点由原 owner 提供；缺失时明确 unavailable，不把当前笔记当历史原文。Basic Memory 工具不可用期间没有宣称入库确认。对原知识服务的重建、账号切换、额外 observer、页面、备份、更多客户端未扩张。
