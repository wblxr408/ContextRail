# Codex / Claude 本机任务接线

2026-11-01：A–D 四阶段完整使用与实际边界见 [CLAUDE_MEM_SELF_USE.md](CLAUDE_MEM_SELF_USE.md)。本文件的第一阶段/历史验收说明按其当时范围保留。

2026-11-01：配置应用现已增加所有权回执、布局升级、多文件回滚和 `client-remove`。按需 Node 依赖、旧无回执配置的处理以及故障解释见 [第一阶段指南](CLAUDE_MEM_STAGE1_USE.md)；下文历史原生验收状态不代表本轮重新验收。

本批实现 A06 所需的绑定读取、Hook 组合和运行时视图刷新，以及 A08 的单一源码布局与独立启停。实际激活状态单独记录；生成配置、协议测试、真实客户端读取和用户原生信任是不同证据。

## 当前项目已配置的入口

本项目的持续实现任务已建立为 `self-use-01a161c7`。任务目标来自本聊天“根据todo做出持续实现”，同时保留自用用途与复用 Basic Memory main 的约束。权威 bundle 与运行库位于忽略目录 `.context-rail/`：

- `.context-rail/active-native-client.json`：当前任务、固定base快照、布局和部署回执；显式刷新后的当前视图另存current_snapshot。
- `.context-rail/bridge/self-use-01a161c7/revision-1/`：原始用户引用、账本、当时 TODO 和状态。
- `.context-rail/clients/self-use-01a161c7/v1/`：registry、入口 runner、两端配置片段。
- `.context-rail/state.sqlite3`：任务证据及租约；长期知识仍使用既有 Basic Memory main。

已在项目内合并 `.codex/config.toml`、`.codex/hooks.json`、`.mcp.json` 和 `.claude/settings.local.json`。没有改个人 API、模型、凭据、全局 Hook 或既有记忆服务。Codex 已有用户级记忆 Hook，因此本项目的新 Codex 处理器使用 `--native-memory`，只交付任务上下文，由原生客户端继续交付原来的记忆结果，避免重复调用。

Codex 新 Hook 要经过原生信任审阅；在本项目运行 Codex CLI 后用 `/hooks` 检查并信任新增项目处理器。**本实现没有写入 trust 状态，也没有使用跳过信任参数。** 新处理器未信任时会被跳过，不能声称任务已自动注入。[OpenAI 官方 Hook 信任说明](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)

本轮实际状态：Claude 已在新原生会话完整读取当前任务与真实 main 历史；Codex 已实际回读任务包/冷页，原 main 历史可见。用户报告完成 Codex 项目 Hook 信任后，新任务处理器调用仍未观察到；A06 因此保留未完成，详见验收记录。

Claude 新会话读取项目 MCP/settings，首次启用项目服务器时遵守其原生项目批准流程。不要据此推断当前已打开的桌面聊天会动态获得新工具。[Claude MCP 文档](https://code.claude.com/docs/en/mcp)

## 任意自用任务的准备与应用

先按 BRIDGE_USE.md 初始化对应 task 并导入真实 Bridge bundle。用导出返回的精确 Rail snapshot，准备一个新布局目录：

```powershell
.venv/Scripts/python.exe -m contextrail client-prepare --workspace D:/my-project --task my-task --session owner --epoch 0 --snapshot <Rail-ID> --output D:/my-project/.context-rail/clients/v1 --memory-command D:/my-project/existing-memory-command.json
```

memory-command 文件是可信本机 argv 数组，不能来自模型参数或历史正文。例如使用已安装工程记忆 Hook 的原命令：

```json
["C:/Users/wblxr/AppData/Roaming/uv/tools/basic-memory/Scripts/python.exe","C:/Users/wblxr/.codex/services/engineering-memory/app/memory_cli.py","hook"]
```

如果该客户端已有独立的原生记忆处理器，准备时增加 `--native-memory-client codex`（或 claude）。新增处理器会明确报告记忆由原生 Hook 独立提供、尚未由本适配器观察，不会再调用记忆命令。没有既有处理器时使用默认组合模式。

准备会注册两个独立 reader：只允许任务所有者用当前租约注册，reader 仍不能写工件、刷新上下文或取得写权。模型名称标为 client-managed-model-unknown，不冒充实际 provider 模型。生成的 entry.py 只指向同一实体源码；升级源码不复制 Bridge 或记忆后台。

检查布局后应用到该项目：

```powershell
.venv/Scripts/python.exe -m contextrail client-apply --workspace D:/my-project --layout D:/my-project/.context-rail/clients/v1
```

可用重复的 `--client codex` / `--client claude` 只准备或应用实际使用的一端。准备结果的 `configuration` 显示实际预算和来源。任务与 Hook 额度写入生成布局，调整后重新准备并应用。MCP 读取和响应限额在服务进程启动时解析，准备时显式指定的限额写入启动参数。见 [项目配置说明](SELF_USE.md#项目配置与初始化)。

重复应用同一布局不会新增处理器。升级时在原有位置替换受管 Hook，保留其他 Hook 的顺序、MCP、用户字段以及 Codex TOML 注释和换行。安装回执记录布局与受管条目。已有同名 MCP 内容不同，或回执中的条目已被手工改动时，应用或移除报告冲突，保留配置供人工核对。变更前的文件备份保存到 `.context-rail/client-config-backups/`。

```powershell
.venv/Scripts/python.exe -m contextrail client-remove --workspace D:/my-project --client codex
```

此命令移除 Codex 的受管条目，原有记忆 Hook 及其他配置照常使用。旧布局没有安装回执时，可通过 `client-apply --adopt-layout <旧布局目录>` 提供原生成文件，只有实际配置中唯一且精确匹配的条目才会纳入管理。

## Windows 本地调用

从 PowerShell 调用时，带空格或中文的路径用单引号包住。PowerShell 5 写出的带 UTF-8 BOM 的项目和个人 TOML 可以直接使用。项目 junction 别名解析到同一工作区和任务库。按 Ref 回读的原文保留 CRLF。

可信记忆命令使用 JSON argv，也可指定完整路径的 `.cmd` 或 `.bat`。批处理参数可包含空格、中文及字面的 `%`、`!` 和 `&`。如果参数本身含双引号或换行，直接配置实际可执行文件或 Python 入口。记忆命令的 UTF-8 JSON 走 stdout，诊断走 stderr。Hook 返回的 stdout 仍只有原生协议数据。

Windows 上，Bridge 校验器、组合记忆 Hook 和配置写入适配器在隐藏控制台的进程树中运行。超时包含 stdin 写入，超时或取消时清理其子进程；命令已结束但子进程还持有输出管道时，也会清理。切换 registry 时遇到短暂的文件占用会重试，持续占用会返回冲突并保留原配置。

## 任务包与冷页

原有 MCP 原文工具继续可用，新增 `context.packet`：

```json
{"snapshot":"<exact-Rail-snapshot>","budget":8192}
```

只接受 snapshot/budget，scope/session 固定于可信启动参数。省略 `budget` 时，使用有效 `context_bytes` 和 reader 读取额度中较小的值；准备布局时显式设置的 `--context-bytes` 随 MCP 启动参数传入。预算为 context JSON 的 UTF-8 bytes，不能超过 reader 的配置读取额度；工具外层元数据另计。无写入、命令执行或源路径覆盖接口。[OpenAI stdio MCP 配置](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

UserPromptSubmit 包装器校验 cwd 与显式绑定，使用原生 session_id 和可选 turn_id；Claude 没有 turn_id 时保留 null，不伪造。transcript_path 不会被打开。输入中的 prompt 不用于选择 task、数据库或导出路径。[两端 Hook 字段与输出](https://learn.chatgpt.com/docs/hooks)、[Claude Hooks](https://code.claude.com/docs/en/hooks)

当工具保存使 task version 前进，受 owner 租约约束的包装器从明确 base_snapshot 刷新运行时视图，记录 base/source/新 snapshot。同一 base/version 重复提交复用视图；不选择另一任务的最新快照。源已变、owner epoch 过期、交接已冻结或硬要求装不下时，原生输出明确 block，不只给历史继续。

当前任务上下文完整保留；历史可以按完整外层 JSON 的 UTF-8 预算缩略，并报告 memory.truncated。memory 失败或超时为显式状态，当前任务仍可提供。返回的 history_control=not_controlled、history_replaced=false；追加上下文不等于控制最终请求或替换旧历史。

Claude 还限制 additionalContext 不超过 10000 个 UTF-16 单元，避免原生客户端把长值改成文件和前 2000 字预览。适配器同时满足字节与字符上限，仅缩略历史；完整任务本身超限时明确 block。[Claude 原生上下文规则](https://code.claude.com/docs/en/hooks#add-context-for-claude)

`native_context.prepared` 审计只保存现有任务库内的调用元数据（客户端、原生会话/回合、快照、记忆状态和字节数），不保存 prompt、transcript、隐藏推理或凭据。它证明处理器准备了输出，不能单独证明模型采取了行动。

## 记忆时限与显式检索模式

当前真实验收中，原混合检索 Hook 在部分长输入上超过包装器的 18 秒预算。这是保留的未完成项，不报告为已成功召回。原始服务与全局检索配置保持原样。

提供一个可选前台命令，直接复用已安装的 Hook formatter 和原共享 main runtime：

```powershell
.venv/Scripts/python.exe -m contextrail existing-memory-hook --home C:/Users/wblxr/.codex/services/engineering-memory --mode text
```

stdin 仍是原生 UserPromptSubmit JSON。默认 mode=hybrid；显式 text 使用原服务的关键词/图谱检索，不报告语义排序、不启动私有模型服务、不更换主模型。实际 main 正向检索用例已经返回历史；某些原生长输入仍超时，未声称所有输入都在时限内。

可把该命令的 argv 保存为另一文件，在 client-prepare 增加 `--task-memory-command <JSON-file>`；它只在这个任务启用时覆盖命令，停用时恢复原 memory-command。本项目 Claude 绑定使用此显式选项；Codex 继续复用其独立原生记忆处理器。

## 独立停用

```powershell
.venv/Scripts/python.exe -m contextrail client-toggle --registry D:/my-project/.context-rail/clients/v1/registry.json --client claude --disabled
```

启用用 `--enabled`。停用后默认组合模式原样透传原记忆结果，任务专用记忆覆盖也撤销；native-memory 模式输出空对象，独立原生记忆处理器照常运行。Bridge 和 main 不被卸载或复制。

## 验收入口

当前行为用例：`python -m unittest tests.test_native_client_context`。真实客户端验收脚本为 examples/native_client_acceptance.py，沿用用户当前模型与登录，不带模型替换、权限/Hook 信任绕过或新 daemon 参数。它单独记录真实工具传输是否精确，以及模型最终回显是否精确，不能混为同一个结论。

原生验收原文日志保存在本地忽略目录 `.artifact-work/native-client-acceptance-*/`，不作为分发包内容。实际结果与剩余激活工作见 openspec/changes/native-client-context/verification.md。
