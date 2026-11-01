# 现有 Codex / Claude Code 启用验收

2026-11-01，Windows 本机 D:/ContextRail；继续原任务 self-use-01a161c7、owner/epoch 和 v1 entry/registry 路径。本聊天用户追加授权迁移并验证原生实际生效。此前 A–D 源码完成不等于本次部署；完整界面与更多客户端仍按需。

## 已启用与迁移

项目 .codex/config.toml、.codex/hooks.json、.mcp.json、.claude/settings.local.json 已迁移到原受管安装回执 .context-rail/client-install.json。旧布局作为显式接管证据，只接管与旧生成服务器/Hook 精确一致的项。新增事件为 SessionStart、PostCompact、PreToolUse、PostToolUse；UserPromptSubmit 沿用原命令。query binding 按精确来源刷新到 5f1a08246ac6411f916f2427ae126e67，新增已核实原生会话作为历史来源，不猜最新日志。current_hook 模式允许本任务后续主会话取得自己的精确身份，不写静态白名单。

Codex 的全局 main/ATR Hook 均保留，项目仍带 --native-memory；Claude 全局 ATR 保留，项目只调用原 main 包装器一次。没有安装新服务、监听器、MCP 目录或更改模型/账号。Codex 通过原生 /hooks 审阅，实际界面显示各新增事件 Active，未直接写信任存储或使用绕过参数。

原项目配置备份：.context-rail/client-config-backups/8b5579bb908246faac3a7fa0c2629b0e。原生成布局备份：.context-rail/client-config-backups/legacy-layout-7c52aedde94f4c7d8deace9be5fce581。去除无效 PostCompact limit 的后续备份：.context-rail/client-config-backups/8dfb436893064e12af9f5912483e909d。备份是本机恢复材料，不提交个人配置。client-remove 只撤销受管项目项，原记忆服务/全局 Hook 不受影响；恢复旧配置前应先核对备份中的 restore-plan 和现有人工改动。

## 当前行为与证据

本次最小迁移/新会话/预算/去重契约 7 项通过（11.236s）。后来真实 Codex 暴露代码模式和 Get-Content -TotalCount 的输入差异，扩展同一新增文件历史用例先失败，修复后通过（3.571s）。没有执行旧套件或将其改名为验收。wheel 构建成功，严格 OpenSpec 验证通过。

| 实际流程 | Codex | Claude Code |
|---|---|---|
| 新会话任务 Hook | 原生回显精确 session/task；prepared=1、returned=1 | 原生 transcript 保留完整内联 JSON；prepared=1、returned=1 |
| 故障解释与产物工具 | context.status、deliverables、file_history 成功 | 相同原生 MCP 调用成功 |
| 真实交付物 | 2 个本地文档、1 个声明分支链接 | 同样 3 个，元数据状态与远程声明分开 |
| 文件历史 | 新代码模式字面量适配后，两次原生读取成功，模型确认自动提醒仅 1 次；原库 prepared=1 | 两次 Read 成功，原 transcript 自动历史正文 1 次；原库 prepared=1 |
| 同任务重复提交 | 后续两个原生请求未增加 native_context.returned，原记忆路由保留 | 最小行为测试验证同快照跳过任务包，原记忆每个提示仍仅调用一次 |
| 压缩恢复 | 当前模型远程 compact 先于 PostCompact 被拒绝，未完成 | 原生 /compact 成功，原监听器记录精确 source_event，delivery state=returned、error=null |

Codex 最终会话 01a196b9-3e0d-70f0-b700-1cac1f777e4d；文件复验沿用此会话，两次 Get-Content 都返回正文，自动历史提醒计数为 1。Claude 新会话 ac6a79fe-b633-45a0-99c8-09bca8191dbc；Claude 实际压缩会话 424534ed-0878-4ca3-8fc8-8a0816342b04，对应原 M 来源 source-event-v1:6a16aae6c277503e9c837d2896c4aaf8，原事件只有 1 条，R 恢复记录为 returned。

原生日志留在本机 .artifact-work/client-activation-*、native-compact-*、native-verify-*、native-file-*；脱敏证据与脚本保存在本聊天 outputs/native-activation-evidence.json 等。原生记录是交付证据，模型不一致的自述不替代记录，也不能从 stdout_flushed 推断后续推理必然使用所有内容。

## 实测促成的修复

- 原上游事务在盘符根目录父路径 mkdir 触发 EPERM；仅补已存在父目录判断，保留原 staging/rollback 主体及来源声明。
- 原无归属条目无法安全升级/撤销，增加显式 --adopt-layout；重复迁移不增项，人工改动使整批保留原状。
- Claude 完整任务超原生内联上限，删除重复展示元数据而保留所有选中正文/Ref，完整视图仍由 context.packet 返回。
- recovery metadata 后加会超预算，改为和正文一同预算；stdout flush 后记普通任务返回，同快照后续提示或压缩后紧接提交不重复任务包。
- 自动历史排除 current_hook 会话自身的读取反馈；默认 explicit 模式保持原来源规则。Codex 只解析已经观察到的字面量 exec_command 包装与 PowerShell AST，不执行代码，不推测动态路径。

## 未完成与外部限制

Codex 原模型 gpt-6.1-sol 的远程压缩返回 400/model_not_found，消息为当前 ChatGPT 账号不支持该模型用于此端点。常规原生模型请求与 MCP 已成功，不能据此声称 compact 也成功。尝试同模型的能力设置时，本机 CLI 拒绝覆盖内置 openai provider；该临时调用未写配置。一次 strict-config 调用另暴露旧 disable_response_storage 字段不被此 CLI 识别，未擅自删除全局旧值。没有用模型/provider/凭据切换或绕过信任制造通过。5.4 保留未完成。

原 main 召回在实际 Claude Hook 中出现 timed_out/failed；当前任务仍可交付，故障状态如实返回。既有原监听器仍采集了本次原生来源和真实压缩事件，但这不等于 main 召回已恢复。本次未替换或重启原服务。

新会话读取已迁移配置。已打开聊天的 MCP 工具目录由客户端缓存，需要原生重载 MCP 或新会话；本聊天不将全局设置复制进其他工作区。

原生控制规则见 [Codex Hooks](https://learn.chatgpt.com/docs/hooks)、[Claude Hooks](https://code.claude.com/docs/en/hooks)；两者的支持事件、信任和内联限制仍由客户端决定。
