# ContextRail

面向现有 Agent 的轻量上下文基础层：保留版本化原始证据、编译按预算加载的工作集，并在不同模型会话之间进行可校验交接。核心目标是让 Agent/模型切换时的上下文重建成本尽量低、且不把上一段会话的推理轨迹当作事实继承下来。

当前为 **0.1 基础架构 + 本地评测脚手架**。核心使用 Python 3.11+ 与 SQLite，提供 Python SDK、只读工具挂载接口和本地 JSON-lines 传输；`contextrail.evaluation` 提供一套 A/B/C 上下文策略的离线/API 评测框架。可选的 `oidc` extra 提供 OIDC Resource Server/membership adapter；默认插件不拥有 IAM。模型调用、登录、组织授权和工具执行由接入方负责。

> **诚实边界（先读这一条）**：核心确定性协议已被 88 项本地测试覆盖并通过。评测框架的采集链路已在 **fixture 模型**下跑通，但 **从未对真实 provider API 跑过端到端评测**，`evaluation-runs/` 目前为空。因此本项目 **尚无任何关于 token、费用、延迟或任务正确率的真实收益结论**。所有此类数字都待按 [工程测评计划](docs/engineering-evaluation-plan.md) 执行后才能声称。

## 快速验证

在项目目录运行，无需安装：

```powershell
python -m contextrail demo
python -m unittest discover -s tests -t . -v
python scripts/check_distribution.py
```

`demo` 使用临时 SQLite 数据库，执行 A→B→A 交接、冷页回读及中文/CRLF 原文校验，然后清理临时数据。输出明确标识 `local_host_simulation`、`model_calls: 0`。这不是实际模型的性能评测。

运行本地评测脚手架（fixture 模型，不联网）：

```powershell
python -m contextrail.evaluation stress --model fixture --output evaluation-runs/smoke
python -m contextrail.evaluation dashboard          # 本地回环端口查看结果
```

如需将 SDK 安装到自己的虚拟环境：

```powershell
python -m pip install --no-build-isolation --no-deps .
```

安装/构建需已有 setuptools>=68 和 wheel。核心没有运行时第三方依赖；只有宿主明确要求插件直接验证 OIDC JWT 时才安装 `pip install 'contextrail[oidc]'`。

## 当前具备的能力

| 模块 | 已实现的行为 |
|---|---|
| Evidence Store | SQLite 持久化；按任务域去重；原文 SHA-256 校验；不可变 revision；精确字节范围读取；当前版本搜索；过期、失效与载荷清理 |
| Task / Snapshot | 固定目标、约束和验收项；带来源和有效状态的完成项、决策、排除项与依赖；显式需求更新；版本比较；不可变证据快照；旧快照失效检测 |
| Working Set | 必需证据固定；可选证据按优先级装入；结构化分块（`StructuralChunker`）和本地词法候选排序（`LexicalEvidenceSelector`）生成字节精确的 optional Selection；未装入内容保留引用；按需指定页面；完整 JSON 输入的预算核算 |
| Context Controller | `ContextController` + `RequestBudget`：为宿主逐轮编译可变证据槽，先从 context window 扣除 system/tool/output/safety reserve，再填工作集；不假设模型记得上一轮请求 |
| Handoff | 准备、编译、送达确认、校验、激活、取消；交接写冻结；session + epoch 租约；旧会话写入拒绝（epoch fencing） |
| Action ledger | 唯一动作 ID 的预留与终态；未完成动作阻止交接；重复预留返回 false |
| Host mount | Python `ContextTools`；绑定单一 scope/session 的 `context.get`、`context.search`、`context.list`、`context.summary_*`；受限本地 JSON-lines 接口 |
| Compaction | `CompactionLifecycle`：snapshot 绑定的摘要生命周期，提交时校验 span 边界与原文可用性；不调用任何模型 |
| Usage / Policy | `UsageLedger`（aggregate 与 request-level 两套账本）、`CacheLayoutPolicy`、provider-neutral `ModelPolicyProvider` 路由契约（记录决策，不自动执行切换） |
| Evaluation harness | `contextrail.evaluation`：A/B/C 上下文策略对照、12 个确定性压力任务、SWE-bench Verified manifest、配对 bootstrap CI 与非劣决策、PriceBook 计费、本地 dashboard |
| 可选 OIDC adapter | 仅当宿主明确要求插件作为 Resource Server 时，提供 JWT 验签、membership 与 `AuthorizedContextTools`；不属于核心依赖 |

默认预算单位为 **UTF-8 字节**，不会将字符估算冒充模型 token。模型适配器可以传入自己的计数函数和单位；还需为宿主 system prompt、工具 schema、历史及输出预留额度。

## Python SDK

以下示例会在当前目录创建 `example.sqlite3`，重复初始化同一任务会报冲突，不覆盖已有数据：

```python
from contextrail import Compiler, ContextTools, Scope, Selection, Store, Target

scope = Scope("local", "my-project", "main", "task-001")
source = Target("session-a", "provider-a", "model-a")
target = Target("session-b", "provider-b", "model-b")

with Store("example.sqlite3") as store:
    lease = store.create_task(
        scope, source, "修改接口，同时保留现有契约",
        constraints=("不得删除已有鉴权检查",),
        acceptance=("原有接口测试通过",),
        allowed_providers=("provider-a", "provider-b"),
    )
    contract = store.put(
        scope, lease, "api-contract", b"GET /items requires authorization\n",
        expected_revision=0,
    )
    snapshot = store.snapshot(scope, lease, (Selection(contract, required=True),))
    handoff = store.prepare(scope, lease, snapshot, target)
    packet = Compiler(store).for_handoff(scope, handoff.id, budget=8192)
    print(packet.body)  # 供宿主发送给目标模型；编译并不代表已经送达
```

宿主实际交付 `packet.body` 后，使用同一数据库继续：

```python
store.acknowledge(scope, handoff.id, target.session, packet.sha256)
store.validate(scope, handoff.id)
new_lease = store.activate(scope, handoff.id)
```

这些调用应放在打开的 Store 生命周期内。`acknowledge` 是可信宿主的送达声明，不证明模型理解，也不能代替真实的模型调用。若发送失败，源 owner 可以 `store.abort(scope, handoff.id, lease)` 解除写冻结。

`ContextTools(store, scope, target.session)` 可直接挂载到宿主工具回调；`definitions()` 返回中立 JSON Schema 定义。宿主根据自己的工具格式转换，不能将它直接当作任意供应商 API 的原生 schema。

### 逐轮上下文治理（ContextController）

宿主拥有模型请求循环时，用 `ContextController` + `RequestBudget` 逐轮重编译可变证据槽：预算先扣除 system prompt、工具 schema、输出与安全 reserve，剩余额度才用于装填工作集。它不假设模型记得任何上一轮请求，因此每次切换模型都从版本化证据 + 显式任务真相重建，而不是继承旧会话的对话历史或隐藏推理。

### 原文与版本规则

- `put(..., expected_revision=0)` 表示只允许首次创建。更新必须传当前 revision；旧值引发 `Conflict`，不覆盖他人工作。
- Ref 的 `start`/`end` 是从零开始、左闭右开的**字节范围**；`end=None` 表示到原文末尾。工件名是逻辑名称，不作为磁盘路径打开。
- `Selection(required=True)` 的原文必须装入。预算不足报 `BudgetExceeded`；不会静默截断目标、约束或必需材料。**注意：当前 required 证据必须是可 UTF-8 解码的文本；required 二进制证据会在编译期报 `InvalidRequest`。**
- `Selection(current=False)` 允许明确引用历史版本。默认 current=True，需要与当前 artifact head 相同。
- 每次内容、需求、结构化任务状态或动作终态变化会递增 task version。旧 snapshot 保留，但不能用于新交接，需捕获新快照。
- `requested=(ref,)` 将当前快照内的冷页临时设为本次必须装入，其他会话的工作集不变。
- UTF-8 文本工具读取直接返回文字；二进制和不对齐字符的原始字节用 base64 返回。编译器只将严格可解码的 UTF-8 原文注入；可选二进制页保留在冷索引中，必需二进制页明确报错。

### 外部工具动作

宿主执行动作前调用 `begin_action`；只有返回 true 才执行。返回 false 表示该 ID 已预留或已完成，不能重复执行。完成后以 `succeeded`、`failed` 或 `cancelled` 写入 `finish_action`，输出原文可单独作为 artifact 保存。

这不是跨数据库/外部服务的 exactly-once 保证。进程在动作执行后崩溃，ledger 仍可能是 pending；宿主必须核实外部结果，再决定终态。ContextRail 不会自动重放未知结果的副作用。

## 评测框架（contextrail.evaluation）

评测框架用于验证一个受约束命题：**在同一宿主、同一模型、同一工具权限下，当任务质量不劣于完整历史基线时，ContextRail 能否降低重复上下文输入、每成功任务成本与端到端时延**。完整方法学见 [工程测评计划](docs/engineering-evaluation-plan.md)。

三种上下文策略共享同一宿主与模型，唯一变量是上下文如何组织：

| 代号 | 策略 | 命题 |
|---|---|---|
| A | 完整历史 | 质量与成本基线 |
| B | 固定窗口截断 | 仅压缩/丢弃信息的风险（纯摘要第二基线尚未实现） |
| C | ContextRail | 版本化 Snapshot + Packet + 冷页精确回读能否在质量不劣时减少重复输入 |

**质量判据是执行式的**，不使用 CodeBLEU 或任何文本相似度指标：SWE-bench 侧用官方 `FAIL_TO_PASS` + `PASS_TO_PASS`；自建压力任务侧用确定性检查（当前为关键事实 containment + 协议边界的真实 Store 校验）。评分先过质量门（成功率非劣、必需事实零遗漏、协议/权限零违反），再比成本。

Dashboard 中的 **L1/L2/L3 是宿主复杂度层级**，不是模型能力分层：

- **L1** ContextRail Minimal Agent（本项目，唯一用于 A/B/C 因果对照）
- **L2** SWE-agent（官方 CLI，需另装，默认不可用）
- **L3** OpenHands Agent Server（官方 Server，需另装，默认不可用）

`contextrail.evaluation.switching` 另有一组 A/B/C 模型 profile 的固定交接轨迹（`A->B`、`A->B->A` 等），属于计划中的 Phase 4 超前实现；当前 **没有基于质量/成本的自动切换触发**，failover 只是模拟事件。

### 已知状态与缺口（诚实清单）

**已跑通（fixture 级）**：A/B/C packet 构造 + 工具 loop + trace/CSV/report 生成；协议确定性测试 X09–X12；配对 bootstrap CI 与非劣决策机（fixture 数据被强制标为 `mechanism_only`，防止把 fixture 当性能结论）；PriceBook 计费；`OpenAICompatibleModel` 是可对接真 provider 的 HTTP 客户端（需人工设置环境变量与 endpoint）。

**脚手架 / 未接通**：SWE-bench 编码 agent 端到端（`run_coding_task` 目前只被单测触发，CLI 无命令实际产出 patch 并交官方 verifier）；外部 L2/L3 agent（adapter 存在但默认不可用、未验证跑过）；switching 多 profile 实验。

**计划写了但代码尚缺**：压力任务侧的完整 oracle 任务卡（`required_facts`/`forbidden_actions`/`must_read`+revision/`acceptance.commands`）；B 的纯摘要基线；TTFT、P50/P95、冷/热缓存分离时延；重复前缀 token 比例、cache 命中率、Packet-bytes/tokens 比、冷页往返统计；usage record 的部分计划字段（provider、timestamp、price_version、request_cost、tool_calls、retries、error_code）。

**已知缺陷（待修）**：`record_request_usage` 用 `require_current=True` 校验快照，会因任务后续写入自增 version 而拒绝记录已发生请求的用量（`store.py`）；`usage_totals` 只聚合旧 `usage_records` 表，漏掉 request-level 账本（`store.py`/`policy.py`）；`switching.py` 违反"缺失即 unknown"原则，把 None 强制成 0；四维 scope 隔离在授权层（`auth.py`）只强制了 tenant+project 两维，branch/task 未校验。

## 本地传输

初始化仅一次：

```powershell
python -m contextrail init --store rail.sqlite3 --tenant local --project sample --task task-001 --session session-a --provider provider-a --model model-a --objective "Keep evidence" --allow-provider provider-a
```

启动只读工具端口，输入输出都是 UTF-8 JSON，每行一个请求/响应：

```powershell
python -m contextrail serve --store rail.sqlite3 --tenant local --project sample --task task-001 --session session-a
```

请求示例：

```json
{"id":1,"tool":"context.search","arguments":{"query":"contract","limit":10}}
```

```json
{"id":2,"tool":"context.get","arguments":{"name":"api-contract","revision":1,"start":0,"end":40}}
```

工件写入由可信宿主通过 SDK 完成，stdio 工具不接受 scope、文件系统路径、SQL 或模型 provider 覆盖，也不开放写入/执行工具。`serve` 的 scope/session 由启动它的宿主绑定；请求 ID 只用于响应关联。

**这是 ContextRail 自己的 JSON-lines 接口，不是 MCP server。** 对 MCP、OpenCode、pi、Claude Code、Codex 等的专用适配尚未实现；不能据此宣称这些宿主已接入或已减少出站历史。

## 项目结构

```text
contextrail/
  models.py       类型、引用、快照包、会话租约、canonical JSON、SHA-256
  errors.py       可跨宿主边界返回的错误类别
  store.py        SQLite 工件/快照/动作/交接事务、状态机、账本
  context.py      确定性的工作集编译与预算
  controller.py   逐轮上下文治理（RequestBudget / ContextController / TurnContext）
  retrieval.py    词法 / 结构化候选选择
  chunking.py     结构化分块（Python 顶层符号、Markdown 标题段、日志段落）
  compaction.py   snapshot 绑定的摘要生命周期（不调用模型）
  policy.py       缓存布局、aggregate/request-level usage 账本、路由契约
  host.py         绑定任务的只读工具入口
  auth.py         可选 OIDC 授权（membership/角色）
  oidc_adapter.py 可选 OIDC Resource Server 适配
  cli.py          初始化、演示、stdio 传输
  evaluation/     A/B/C 评测框架、压力任务、SWE-bench、switching、dashboard
tests/            持久化、并发、隔离、预算、交接、评测回归（88 项）
scripts/          临时隔离的分发包验证
docs/             架构说明、实现清单、评测计划与原有研究资料
```

## 身份与授权

插件要求宿主在挂载前提供可信 Principal 或 capability，并在插件请求时对 `principal + scope + action` 返回授权结果。请求中的 `tenant`、`project`、`user_id` 和 `role` 都不是身份凭据。插件必须把宿主结果绑定到 Scope/session，不能让模型覆盖它。默认核心不验证 JWT、不管理 membership，也不运营 IAM。

详细责任划分见 [插件范围审计](docs/plugin-scope-audit.md)。当目标宿主明确要求直接 OIDC 对接时，可使用 [可选 OIDC 适配器](docs/security/keycloak-oidc-integration.md)。当前 `ContextTools` 和 `serve` 保留为受信任本地宿主接口；不得将它们直接作为未认证的网络 API 暴露。

> 授权隔离现状：数据分区是四维的（`Scope(tenant, project, branch, task)`），但可选 OIDC 授权层当前只按 tenant + project 判定成员角色，尚未强制 branch/task 级隔离。需要更细粒度隔离的宿主应在自己的授权层补齐。

## 边界与数据保护

SDK 的原始 `Store` 和 `ContextTools` 是可信宿主组件；外部网络请求须在释放数据或工具前执行宿主提供的授权结论。直接 OIDC 验证与 `AuthorizedContextTools` 仅在用户明确选择该可选适配器时使用。同机上能读数据库的进程可以看到原文；SQLite 未加密。需要由宿主提供文件访问权限、数据分级及供应商审批。当前 provider allowlist 作用于整个任务，尚无逐工件数据分级。

核心不联网、不调用模型、不读取密钥、不上传遥测；它不会自动识别或清除原文中的密钥/PII。不要直接存入不允许保留的秘密。返回工件始终作为不可信证据，宿主不能提升为系统级指令。

过期或 invalidated 的内容立即拒绝回读；`purge(scope)` 只清理不再被有效 revision 引用的载荷。版本墓碑、必要快照与元数据保留；SQLite 空闲页和 OS 备份不是安全擦除范围。任务目标、约束本身也可能敏感，保留策略须覆盖整个数据库。

模型切换策略、自动摘要、语义检索、缓存/KV 和在线 benchmark 不在 0.1 基础实现中。评测框架会在每次调用前重新编译 C 策略的上下文槽，并为每个实际请求记录 packet 与请求 digest、provider request ID、延迟和可空的 usage 字段；缺失 usage 保持为 `unknown`，不会写成零。它仍是 OpenAI-compatible 的最小评测宿主，尚未执行真实模型实验，不能声称已有正确率或成本收益。

## 文档

- [基础架构说明](docs/architecture.md)
- [实现细节清单](docs/implementation-inventory.md)
- [插件范围审计](docs/plugin-scope-audit.md)
- [工程测评计划](docs/engineering-evaluation-plan.md)（面试/落地用最小执行版）
- [逐轮上下文治理实现审计](docs/context-governance-implementation.md)
- [评测环境说明](docs/evaluation-environment.md)
