# Design

## Context

见 proposal.md。现有 Store 事务、版本和 ContextTools 为复用边界；工具执行权属于宿主。工作区含既有研究修改，本批只新增适配代码与必要核心扩展。

## Goals / Non-Goals

Goals：完成 A01/A02/A04/A05/B01/B02/B05/B07 的本地可用闭环。Non-Goals：安装个人客户端 Hook、自动执行命令、采集长期记忆、改变 workspace 写权或扩展其余 TODO。

## Decisions

1. `.contextrail.toml` 以工作区为锚；显式覆盖 > marker > 默认，未知键拒绝。身份由可信配置/本地 Git 元数据决定，模型参数不能重绑。repo identity 与 worktree identity 分开；Scope.branch 使用稳定 worktree 身份，分支名只作信息。
2. stdlib 实现配置、身份、结果接收和渲染；MCP 使用官方 SDK v1（`mcp>=1.28,<2`）的 low-level Server/stdio，作为可选 extra。核心无新增必选依赖。手写 JSON-RPC 缺少完整协议维护能力；第三方 FastMCP 独立框架增加不必要层次。官方 SDK 的协议契合、生态和维护优先；Python/SQLite 已确定，不迁移栈。部署/退出成本是删除可选 adapter，性能和资源没有新增实测结论。其余选型维度沿用核心既有决定，不制造评分。
3. 可信宿主提交 UTF-8 或 base64 编码结果；记录 command argv/cwd/起止时间、exit/timeout/partial、独立 stdout/stderr refs。新建 `put_many` 事务复用 Store 写逻辑，保证结果三件套同成同败；相同 call ID 完整重送幂等，异内容拒绝。
4. 完整原文始终保留；渲染只缩略 preview，最终 JSON 完整序列化并按 UTF-8 bytes 控制。预算连必需元数据都装不下时明确报错。二进制可回读，不冒充 UTF-8。
5. 分类遵循 timeout/unknown/partial/succeeded/failed/no_match；`no_match` 仅显式允许的 rg/grep 无匹配形态，其余 rc=1 即使有 stdout 仍失败。
6. MCP 仅挂载已有读取工具及能力报告；支持性来自可信宿主证据，客户端名称不构成 Hook/历史替换证据。未提供证据的 Hook/最终请求控制均 unknown；不执行未经确认的改写。
7. capture.ignore 在保存前核对 cwd 与宿主声明的 source_paths；命中时整条拒绝。自由文本不能反推出所有文件访问，required 路径仅声明待加载材料，自动启动注入留给后续接线。

## Risks / Trade-offs

- 大输出接收需要宿主已收集 bytes；流式收集、进程生命周期和执行权限不属于此批。
- 本机路径身份不等于跨机身份；显式 project ID 可共享知识命名，但 worktree/task 状态保持隔离。
- SDK 只证明协议调用成功；当前 Codex 桌面没有会话内动态 MCP 注册接口，因此真实客户端契约用官方 ClientSession 调用 stdio 子进程验证，提供个人注册配置例子，实际桌面会话接线属于 A06。
- 现有未修改功能的回归套件不在本轮授权范围，仅执行当前新增行为 TDD 和必要静态/规范检查。

## Migration Plan

新模块、可选依赖和 CLI 子命令可独立撤销。Store 无新增表或 schema 迁移；put_many 复用相同版本写语义。配置中的证据库不加入长期记忆库。

## Evidence

- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/docs/low-level-server.md
- https://github.com/modelcontextprotocol/python-sdk/blob/v1.28.0/docs/client.md
- docs/self-use-integration-todo.md 的指定八项；功能测试由其消费者契约导出。
