# 自用：保存工具结果并按引用回读

本批实现 A01、A02、A04、A05、B01、B02、B05、B07。宿主执行工具后，把结果交给 `OutputRecorder` 或 `capture-result`；它先完整落库，再返回有界 JSON。MCP 只提供读取。个人 Codex/Claude Hook 接线属于 A06；本批未修改个人配置，尚不会自动拦截你当前聊天里的所有工具输出。

## 项目配置与初始化

Python 3.11+。在本仓库安装可选 MCP adapter（核心仍无必选依赖）：

```powershell
uv pip install --python .venv/Scripts/python.exe -e '.[mcp]'
Copy-Item .contextrail.example.toml .contextrail.toml
.venv/Scripts/python.exe -m contextrail project-info --workspace D:/ContextRail
.venv/Scripts/python.exe -m contextrail project-init --workspace D:/ContextRail --session local-host --objective "完善我的自用 demo"
```

`project-init` 对一个 scope 只执行一次；已有任务报冲突，不会覆盖目标或租约。返回 `scope`、`lease`、解析后的库路径、仓库与 worktree 身份。之后使用当前 owner 的 session/epoch 提交结果；既有 SDK 的交接流程负责变更 owner，MCP 不注册或抢占会话。

配置见 [.contextrail.example.toml](.contextrail.example.toml)。从 `--workspace` 指定目录向上找最近的 `.contextrail.toml`，也可 `--config` 指定。所有配置相对路径和相对 `--store` 都锚定 marker 所在目录；无 marker 时锚定传入目录。`--workspace` 是查找起点，marker 的 `project.workspace` 决定实际工作区。

`--task`、`--project-id`、`--store`、`--response-bytes`、`--read-bytes` 优先于配置；`project-info.sources` 显示来源，覆盖只对本次调用有效：

```powershell
.venv/Scripts/python.exe -m contextrail project-info --workspace D:/ContextRail --task investigate --response-bytes 2048
```

`project.id` 可显式指定；默认使用本机 Git common dir 身份，linked worktree 共享 repository ID。`Scope.branch` 在这些新入口中使用 worktree ID，分支名另放在 `identity.branch_label`，因此换分支不会意外创建另一个任务。不同 worktree、不同 task 各自隔离。非 Git 目录以规范绝对路径标识；Windows 大小写与 junction 解析为相同身份，同时保留 `requested_workspace` 供展示。本机目录移动或 Git 初始化可能改变默认 ID；需要固定项目命名时设置显式 ID，跨机状态迁移仍需专门设计。

`context.required` 目前声明必读路径，供后续 Bridge/启动注入消费，不会自动打开文件。`capture.ignore` 使用相对工作区的 glob（`/` 分隔，`*` 可跨目录；Windows 不区分大小写）。接收器检查 cwd 及宿主声明的 `source_paths`，命中忽略规则或越出工作区就拒绝整条结果。工具输出可能来自任意外部来源；接收器无法从自由文本推断它读了哪些文件，宿主应如实提供来源。它不递归扫描目录。

## 交给接收器的结果

`observed-result.json` 示例：

```json
{
  "call_id": "build-001",
  "tool": "shell",
  "argv": ["python", "build.py"],
  "cwd": "D:/ContextRail",
  "started_at": 1792660000.0,
  "finished_at": 1792660001.0,
  "exit_code": 1,
  "timed_out": false,
  "partial": false,
  "source_paths": ["build.py"],
  "stdout": "开始构建\r\n普通输出\r\n",
  "stderr": "最终错误：依赖缺失\r\n"
}
```

每一路输出必须单独提供：UTF-8 `stdout`/`stderr`，或 `stdout_base64`/`stderr_base64`，两种表示不能同时出现。对于真实进程输出，推荐从 bytes 编码 base64，避免宿主的文本模式先改写 CRLF 或丢弃非 UTF-8 字节。未知退出码用 `null`，缺失超时/partial 默认 false。时间必须有序且有限；`argv` 记录直接执行的命令，不自动解析 `sh -c`、管道或命令拼接。

```powershell
.venv/Scripts/python.exe -m contextrail capture-result --workspace D:/ContextRail --session local-host --epoch 0 --result observed-result.json
```

也可 `--result -` 从 UTF-8 stdin 读取。只把这条命令返回的小 JSON 发给模型。**不要先把完整 stdout/stderr 发给模型再保存**，否则已产生的历史无法通过本接收器撤回。

返回的 `result_ref` 可读取执行信封：完整 argv、cwd、时间、source_session、scope、状态和两路输出 refs。`stdout.ref`、`stderr.ref` 可读取完整原文；call ID 对应逻辑名称 `tool-result/<call_id>/result`，首次 revision 为 1。三件证据同一事务提交。相同 call ID 与相同完整内容重送不新增 revision 或 task version；不同内容、来源会话或元数据报 `conflict`。call ID 每次真实执行使用新值，重送同一次观察保留旧值；接收器不会重执行命令。

`response_bytes` 约束完整序列化 JSON 的 UTF-8 字节数（不含 CLI 行分隔符），预算含所有元数据和引用，预览保留头尾。`truncated` 说明展示不完整，完整 bytes 不裁剪；binary 预览为空，按 Ref 原样读取。预算连必要引用都装不下时返回 `budget_exceeded`，本次不入库。`read_bytes` 限制一次 `context.get` 的原始内容范围；协议 JSON/base64 开销另计。

分类保留原始 exit code 与两路输出：

| 条件（按顺序） | status |
|---|---|
| timed_out=true | timed_out |
| partial=true | partial |
| exit_code=null | unknown |
| exit_code=0 | succeeded |
| tool=shell，直接 argv[0] 是 rg/grep（含 .exe），rc=1 且两路输出均空 | no_match |
| 其他非零退出 | failed |

例如构建失败即使打印普通 stdout，仍为 failed；`sh -c 'rg ...'` 的 rc=1 不推断为无匹配。partial 只说明结果不完整，仍须检查原始 rc。rg 的 stderr 非空也不降为 no_match。

## 在自己的宿主循环中调用

```python
from contextrail import Lease, Store
from contextrail.project import load_project
from contextrail.tool_results import OutputRecorder

config = load_project("D:/ContextRail")
# observed 是宿主已经执行得到的上述字典；数据库/任务事先初始化。
with Store(config.store) as store:
    small = OutputRecorder(store, config.scope, Lease("local-host", 0),
                           config.workspace, ignore=config.ignore).capture(
        observed, budget=config.response_bytes)
    # 把 small 作为这一次工具结果；执行权限和命令生命周期仍由宿主管理。
```

## MCP 注册与回读

stdio 启动命令：

```powershell
D:/ContextRail/.venv/Scripts/python.exe -m contextrail mcp --workspace D:/ContextRail --session local-host
```

注册前需完成安装和 project-init。采用绝对可执行文件路径；以下为通用 JSON 格式客户端的 server 配置片段，需放在该客户端支持的 MCP 配置位置，不是 Codex TOML：

```json
{
  "mcpServers": {
    "contextrail": {
      "command": "D:/ContextRail/.venv/Scripts/python.exe",
      "args": ["-m", "contextrail", "mcp", "--workspace", "D:/ContextRail", "--session", "local-host"]
    }
  }
}
```

提供 `context.get`、`context.search`、`context.list`、`context.summary_search`、`context.summary_get`、`context.capabilities`。scope/session 固定在启动参数，模型不能传入另一 task、SQL 或磁盘路径。`context.list` 需要 SDK 生成的 snapshot ID；未有 snapshot 时用 `context.search` 找当前 artifact。

将返回的 Ref 直接作为 `context.get` 参数，例如：

```json
{"name":"tool-result/build-001/stderr","revision":1,"start":0,"end":null}
```

大输出按零基、左闭右开的字节范围分段回读，每段不超过 read_bytes；`start=max(0,bytes-2048),end=null` 可读末尾。未对齐 UTF-8 字符或二进制会返回 base64，不能把字节偏移当字符偏移。返回内容标为 `untrusted_evidence`。错误使用 `isError=true` 和 JSON `error.code/message`，不把错误变成空结果。

`context.capabilities` 分开报告读取、事件捕获、启动注入、压缩钩子、阻止、参数改写、最终请求控制及其证据。本 adapter 证明读取可用，其余默认 unknown，`history_control=not_controlled`、`history_replaced=false`。可信宿主 SDK 可用 `CapabilityEvidence` 声明已验证能力；模型参数和客户端名称/版本不构成证据，能力可用本身也不表示已执行历史替换。

## 本批验证

针对性行为用例在 [tests/test_self_use_output_loop.py](tests/test_self_use_output_loop.py)，覆盖配置与临时覆盖、同名目录、linked worktree、Windows 大小写/junction、原文重开库回读、重送/冲突、原子失败、忽略与越界、预算、非法时间戳拒绝和状态分类，以及官方 MCP `ClientSession` 的真实 stdio 调用、旧 revision、分页和任务边界。MCP 用例需要安装可选 extra，未安装时只跳过该协议用例。只运行本批新增用例：

```powershell
.venv/Scripts/python.exe -m unittest tests.test_self_use_output_loop
```

旧回归套件本轮未执行；官方 SDK 客户端调用已验证，个人桌面会话注册和 Hook 拦截仍待 A06。来源参考与其余批次见 [自用 TODO](docs/self-use-integration-todo.md) 及 `openspec/changes/self-use-output-loop/`。
