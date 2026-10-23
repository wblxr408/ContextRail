# Bridge 要求单向导入与必需工作集

这一批完成 TODO A03、D02，复用本机现有 context-bridge 的 requirements.py 校验账本、修订与接收回执。Bridge bundle 始终是要求权威；Rail 保存版本证据和派生上下文，不回写 bundle，也不会代接收者填写 aligned。

## 导入与读取

先准备真实 Bridge bundle：requirements.json 需有 schema=1、明确 task_id/snapshot_id，以及 source.path/quote/locator、逐条 revision/scope/always_load/status/acceptance/evidence。格式与校验规则沿用已安装 Bridge，不从历史摘要重建要求。

Rail 的 task 必须和 bundle.task_id 相同。在项目根目录执行，以下 demo-task 换成真实 task_id：

```powershell
.venv/Scripts/python.exe -m contextrail project-init --workspace D:/ContextRail --task demo-task --session local-host --objective "Bridge 派生任务"
.venv/Scripts/python.exe -m contextrail bridge-import --workspace D:/ContextRail --task demo-task --session local-host --epoch 0 --bundle D:/my-checkpoints/revision-1 --scope implementation
```

已有 task 则直接导入，不重复初始化。导入使用当前 owner 的 session/epoch；不会抢占或创建另一任务。默认校验器为用户主目录的 `.codex/skills/context-bridge/scripts/requirements.py`，可用可信宿主参数 `--bridge-validator C:/.../requirements.py` 覆盖。没有安装时明确报 unavailable；它不是模型可传入的工具参数。

返回值中 `bridge_snapshot` 是原 Bridge snapshot_id，`snapshot` 是此次发布的 **Rail snapshot ID**，`manifest_ref` 可经已有 MCP `context.get` 回读。保存这个精确 Rail ID，随后使用：

```powershell
.venv/Scripts/python.exe -m contextrail bridge-packet --workspace D:/ContextRail --task demo-task --session local-host --snapshot <Rail-snapshot-ID> --budget 8192
```

结果的 `context` 是供宿主注入的 JSON 工作集，`units` 为该完整 context JSON 的 UTF-8 字节数，CLI 外层导航字段另计。包含有效目标、约束、当前验收以及固定的 goal/constraint/always_load 原始行。当前 goal 来自 Bridge，有效约束/验收与首次导入前的独立宿主约束/验收合并；后续修订只替换 Bridge 派生部分，保留宿主要求。首次运行时目标另存 manifest.base_requirements 供追溯，不再与 Bridge 的新目标争夺权威。应为 Bridge 任务使用专用 task。

其他选中 deliverable 按预算可选装入；延期 scope、superseded 行和完整来源仍保存在 manifest 中。必需项放不下会报 budget_exceeded 并列出固定 R ID，不会静默缩略。文件正文不自动成为系统指令，仍作为原始证据处理。

## 版本、原文与新鲜度

导入先捕获 requirements.json 及声明的 source/evidence 到临时副本，再在该副本调用原 Bridge 校验器；持久化相同 bytes。只读取声明文件，不递归扫描其他文件，不把目录正文用于选择输出位置。总量最多 64MiB、账本最多 2MiB、最多 1024 个来源文件；路径和 junction 解析不得越出 bundle。

manifest 保留原 task/snapshot/scope 和每条要求的 Rail Ref、Bridge revision/status/scope。逐条 Ref 的正文保留原完整 row，包括 quote、locator、superseded_by、进展和验收。`ledger_ref` 是原始账本；`sources` 按包内路径映射原始文件 Ref。CRLF 和二进制 evidence 原样保存。Bridge revision 与 Rail artifact revision 是两种不同身份，不可互换。

完全相同的 checkpoint/scope 重送返回同一 Rail snapshot，不增加 task version；同身份内容变化报 conflict。新 checkpoint 复用原 validator 的 --previous，要求 ID 不丢失、revision 不倒退、实质内容变更提升 revision。所有证据、派生任务要求、当前指针和 snapshot 同一事务提交，中途存储失败全部回滚。

编译/回执会检查当前导出指针及原 bundle 的实际 bytes。源变化、被删除、另一个导出取代或 Rail task version 前进时，拒绝以旧 snapshot 编译当前任务，不自动选“同项目最新”替代。历史 Ref 仍可回读。`export_current` 仅表示 Rail 当前导出指针，不保证源在返回后永远不变；实际 packet 会重新核对来源。

## 接收者逐条回执

接收者阅读 packet 与原文后，在包外自己填写 receipt.json。格式仍为 Bridge 原格式，其中 snapshot_id 使用 **Bridge ID**，checks 必须恰好覆盖当前 brief 中全部 ID（包括当前 scope 的可选 deliverable）：

```json
{
  "task_id": "demo-task",
  "snapshot_id": "demo-task/revision-1",
  "scope": "implementation",
  "checks": [
    {"id":"R002","revision":1,"result":"aligned","interpretation":"我实际理解的要求","next_action":"据此准备的具体动作"}
  ]
}
```

示例仅展示单条结构，真实 checks 必须逐条填写，不应直接复制成自动对齐声明。校验命令传入 Rail ID：

```powershell
.venv/Scripts/python.exe -m contextrail bridge-receipt --workspace D:/ContextRail --task demo-task --session local-host --snapshot <Rail-snapshot-ID> --receipt D:/receiver/receipt.json
```

此命令从已存证据还原临时 bundle，再调用原 Bridge receipt。旧 revision、错 task/snapshot/scope、缺项、重复或未 aligned 均拒绝。成功为 coverage_verified，`semantic_alignment=receiver_attested_not_machine_proven`：不证明自然语言理解正确，不等于交接激活、写权取得或执行验收通过。

## SDK

```python
from contextrail import Lease, Store
from contextrail.project import load_project
from contextrail.bridge import BridgeEvidence

config = load_project("D:/ContextRail", overrides={"task": "demo-task"})
with Store(config.store) as store:
    bridge = BridgeEvidence(store, config.scope, "local-host")
    exported = bridge.import_bundle("D:/my-checkpoints/revision-1", "implementation", Lease("local-host", 0))
    packet = bridge.packet(exported["snapshot"], budget=8192)
```

本批用例：`.venv/Scripts/python.exe -m unittest tests.test_bridge_requirement_evidence`。完整实现与实际证据见 openspec/changes/bridge-requirement-evidence/verification.md。个人 Codex/Claude Hook、默认事件采集与后续恢复入口继续按 TODO 推进。

## 终端导航（A07）

同样绑定 --workspace/--task/--session：

```powershell
.venv/Scripts/python.exe -m contextrail status --workspace D:/ContextRail --task demo-task --session local-host
.venv/Scripts/python.exe -m contextrail checkpoints --workspace D:/ContextRail --task demo-task --session local-host --offset 0 --limit 20
.venv/Scripts/python.exe -m contextrail search --workspace D:/ContextRail --task demo-task --session local-host --query R002
.venv/Scripts/python.exe -m contextrail continue --workspace D:/ContextRail --task demo-task --session local-host --snapshot <Rail-snapshot-ID> --budget 8192
.venv/Scripts/python.exe -m contextrail explain --workspace D:/ContextRail --task demo-task --session local-host --snapshot <Rail-snapshot-ID> --budget 8192
```

status 显示当前任务目标/约束/验收与快照数量。checkpoints 按发布顺序分页，每条返回 snapshot、task_version、store_current、Bridge 来源以及 resume.argv/resume.powershell。store_current 仅说明任务版本一致，source_current=not_checked 表示目录还未核对磁盘来源；continue/explain 才完整核对来源并编译。列表不会替你把最新快照当成指定游标的替代。

可直接复制 argv 执行，或复制已逐项转义的 PowerShell 字面量命令。continue/explain 不接受缺失 snapshot，不创建聊天、不激活交接、不改变 owner/epoch；旧版本和其他 task 的快照明确拒绝。search 复用当前 scope 的字面搜索，历史来源仍按具体 Ref 回读。

search 显式返回 historical_exports_may_match=true、source_current=not_checked；命中历史导出不能据此宣称它是当前 Bridge 要求，当前要求需通过指定 snapshot 的 continue/explain 核对。
