# claude-mem 自用适配：A–D 使用指南

2026-11-01。用户确认完成 A–D 后已追加启用本地界面，见 [CLAUDE_MEM_UI.md](CLAUDE_MEM_UI.md)；更多客户端及其余后置 P1/P2 继续按需启用。原 M/main、Bridge 和 Rail 仍各自拥有原知识、要求和任务证据。

## 已实现入口

| 场景 | 入口 | 真实来源与边界 |
|---|---|---|
| 配置升级/撤销/故障解释 | client-apply、client-remove、status / context.status | 原配置回执、原队列/control/index、native prepared/returned |
| 无模型显式记录 | memory-save | 原 M.retain；原文和确定性摘录；不自动归纳 |
| 压缩后继续 | PostCompact → SessionStart(compact)/PreToolUse/PostToolUse/UserPromptSubmit | 原监听器 context.compacted 身份 + 精确 Bridge base；准备、stdout 返回、模型消费分别记录 |
| 文件旁的历史 | PreToolUse；file-history / context.file_history | 原 M/R 关联路径时间线；同会话/同来源版本不重复，普通读取权限保持原生 |
| 命中后的回读 | history-details、history-neighborhood / 对应 context.* | 返回命中直接携带 navigation 参数，部分来源失败逐项显示 |
| 找到交付物 | deliverables、deliverable / 对应 context.* | 原任务 artifact/task_state；路径、版本、来源和缺失/改变状态 |
| 显式登记交付物 | deliverable-register | 可信宿主写入原 Rail 版本化 artifact；无新增模型写工具 |

第一阶段详细维护方法见 [CLAUDE_MEM_STAGE1_USE.md](CLAUDE_MEM_STAGE1_USE.md)，本轮完成审计见 `openspec/changes/claude-mem-self-use-ergonomics/completion-audit.md`。

## 运行依赖

原 Python 3.11+/SQLite 继续使用。所选上游 TypeScript/JavaScript 按需调用 Node 22.6+ 的原生类型擦除，可用 `CONTEXTRAIL_NODE` 指定受信任可执行文件。无 npm/Bun 安装、HTTP worker、新后台 observer 或新知识库。

PowerShell 字面路径适配使用已安装 `pwsh.exe` 的 AST，仅解析而不执行输入命令。仅接受单条 Get-Content 的字面 Path/LiteralPath 和 Raw；变量、管线、表达式、不支持参数或缺少解释器时跳过可选增强。结构化 Read/Edit/Write 与上游支持的 MCP read/view/cat 路径也可使用。多文件入口当前只增强第一个可靠路径，其他文件仍可显式 file-history 回读。

可选 MCP SDK 支持 `mcp>=1.28,<3`。当前 SDK 2 使用构造器 handler，1.x 保留装饰器注册；业务读取与参数验证共用。没有修改 Basic Memory 环境或替换其他程序的依赖。两端新工具均经过真实 stdio 调用；SDK 1.30 另用项目内隔离环境验证。

## 为一个明确任务启用原生辅助

先使用原 Bridge 流程导入用户要求，保留其精确 base snapshot；准备可信 QueryBinding，例如：

```json
{"schema":1,"workspace":"D:/my-project","task":"my-task","memory_home":"C:/Users/wblxr/.codex/services/engineering-memory","native_sessions":[{"client":"codex","session":"EXACT_NATIVE_SESSION"}],"memory_mode":"text"}
```

native_sessions 只填实际使用且明确关联的会话。新会话身份不同须更新可信绑定，不按 cwd 猜另一个会话；Hook 每次读取绑定，MCP 在服务器启动时读取绑定，修改后须重连相应 MCP。仅主会话路径，未绑定子线程明确 unavailable。

```powershell
python -m contextrail client-prepare --workspace D:/my-project --task my-task --session owner --epoch 0 --snapshot EXACT_BASE --output D:/my-project/.context-rail/clients/v2 --query-binding D:/my-project/query-binding.json --context-assistance --native-memory-client codex
python -m contextrail client-apply --workspace D:/my-project --layout D:/my-project/.context-rail/clients/v2
```

只有该客户端已有独立原生记忆 Hook 时才填 native-memory-client；否则沿用原 memory-command 配置。新布局仍受原客户端 Hook 信任和项目批准控制。旧无归属回执条目默认保持未认领；用户显式提供旧生成布局时，可使用 `client-apply --workspace <project> --layout <desired-layout> --adopt-layout <old-generated-layout>` 接管精确一致的旧服务器/Hook。旧值有人工修改或重复时整批报冲突，原配置不写入。

已有任务需要用于新原生主会话时，可在可信 registry 的对应 binding 设置 `context_assistance: true`、绝对 `query_binding` 路径和 `native_session_binding: "current_hook"`。当前身份只来自原生 Hook payload，与精确任务和工作区一起使用；不会猜测最新日志或改写静态历史白名单。默认模式仍是 explicit。自动文件提醒排除当前会话自己的读取记录，避免刚读文件又触发新历史；固定历史会话和 R 任务来源仍可补入，显式 MCP 历史读取不受此自动提醒过滤影响。

Claude 需要压缩传输时，保留所有选中正文及精确 Ref，仅省略重复的每条展示元数据，标记 transport_view 并提供完整 context.packet 入口。恢复来源 metadata 与正文共同参与预算选择。必需要求仍由原编译器判断，不裁剪成摘要。PostCompact 不设置无效的 additionalContextLimit，避免原生配置告警。

增强启用后，普通任务包也在 stdout flush 后登记 native_context.returned；同会话同快照后续提交保留原记忆结果，跳过重复任务包。新快照或明确的新压缩来源仍会返回。Codex 代码模式支持已观察到的 `text(await tools.exec_command({...}));` 和直接 await 形式，仅接受对象内 JSON 字面量值；Get-Content 支持字面量路径及 TotalCount/Head/Tail 整数，动态表达式或复杂包装继续跳过，不执行解析内容。

PostCompact 只标记。原监听器的准确 context.compacted 尚未入库时显示 awaiting_original_compaction；后续可返回上下文的事件再尝试。原源失效、租约不符或预算不足明确报缺口，不选其他最新快照。控制状态保存在原 Rail DB；上游 marker 被删除不能证明送达。stdout flush 后记录 returned，模型实际使用仍 unknown。准备后进程损失可在 60 秒 claim 到期后恢复；发送与回执之间的损失保留传输不确定性，不声称物理 exactly-once。

## 文件历史和连续回读

```powershell
python -m contextrail file-history --workspace D:/my-project --task my-task --session reader --query-binding D:/my-project/query-binding.json --file 'D:/my-project/src/中文 file.py'
python -m contextrail history-details --workspace D:/my-project --task my-task --session reader --query-binding D:/my-project/query-binding.json --input selected-refs.json
python -m contextrail history-neighborhood --workspace D:/my-project --task my-task --session reader --query-binding D:/my-project/query-binding.json --input selected-neighborhood.json
```

selected-refs.json 为 `{"refs":[<返回的 ref>]}`，selected-neighborhood.json 为 `{"ref":<返回的 ref>}`。也可直接复制返回的 navigation.tool/arguments 调用 MCP。现有 context.query 命中已带同样参数。

覆盖的是所绑定 M/R 原时间线中明确关联路径的记录，不将所有同名记忆笔记认作同一文件。三源详情继续使用 context.get、memory.stream_get、basic-memory.read_note；M note 没有可用历史版本/事件时间锚点时明确说明，不假造邻域。其他主记忆检索继续走原 context.query。缺少可选来源只显示 history gap；不触发整文件 deny、自动执行指令或模型调用。

## 登记与打开交付物

把已保存任务来源的精确 Ref 放入宿主登记 JSON：

```json
{"registration_id":"report-1","source":{"name":"saved-host-result","revision":1,"start":0,"end":null},"fields":{"deliverables":[{"path":"D:/my-project/results/report.csv","title":"结果报告"},{"url":"https://github.com/example/repo/pull/42","title":"相关 PR"}]},"expected_revision":0}
```

```powershell
python -m contextrail deliverable-register --workspace D:/my-project --task my-task --session owner --epoch 0 --input register.json
python -m contextrail deliverables --workspace D:/my-project --task my-task --session reader
python -m contextrail deliverable --workspace D:/my-project --task my-task --session reader --input exact-product-ref.json
```

登记只接受已授权宿主的当前租约和原任务来源。项目外输出需由宿主显式 `--allow-root <目录>`，模型参数不能扩大目录。相同 registration_id/内容重放一个业务效果；改变登记需携带实际 expected_revision。旧记录可按 record_ref 精确回读，原来源版本不被替换。记录不复制用户产物，也不上传或发布。

列表返回 ref、record_ref、source、task/version、open_path 或 open_url、markdown_link，可直接在当前聊天展示链接。文件显示 metadata_matches、changed、missing 或 unavailable；metadata_matches 仅表示文件身份、大小、mtime/ctime 匹配，不能当作正文验证。改变后打开的是当前文件，不冒充已登记版本；缺失时保留原位置和来源。HTTP(S) 链接只是 declared_link_not_verified_publication，不访问远端，不把 URL 当作发布成功。

原 task_state 中的 work_product/work_products/deliverable/deliverables/*_url 声明经同一上游字段和去重函数投影，仍标声明性质。列表最多扫描 128 个登记记录，截断与分页可见。完整界面不是依赖。

## 当前交付边界

本机内存测量与优化见 openspec/changes/claude-mem-self-use-ergonomics/memory-verification.md。没有 marker 时采用与固定上游命名一致的负向快速判断，普通 Hook 不再启动 Node；真实标记检查/写入/消费仍复用原 TS。非文件工具跳过原本必定返回空的文件提取调用。不增加缓存、监听器或常驻进程，不更改原 Hook 命令及信任。该优化不减少模型/向量加载内存，也不承诺所有事件均无短时峰值。

源码在本机隔离任务分支实现并同步到 D:/ContextRail；保留其他在途改动。此前 cloud exec 实际返回环境不可用，本轮在本机完成宿主验收。用户追加启用后，已显式接管原 v1 项目接线，Codex 通过原生 /hooks 审阅启用新增事件；没有直接写信任 hash、替换账户/模型或改原 M app。其他工作区需绑定其自身任务，不能套用本项目来源。实际启用结果及限制见 openspec/changes/claude-mem-self-use-ergonomics/activation-verification.md。

只执行本批新增行为契约、用户追加要求的真实客户端流程及构建/schema 检查；旧回归套件、smoke、版本/import/启动/健康探针、文件存在性或 hash/措辞检查、广泛费用/内存基准均未运行。原 A–D 实现阶段没有模型调用；追加启用阶段使用既有模型与登录完成原生调用。Basic Memory 写工具仍不可用，待入库经验保留本地草稿，不声称索引确认。
