# Native client context：实际验收

## 2026-10-23追加：Codex当前任务Hook已观察

本轮“直接下载开源项目再适配”的turn，宿主实际注入contextrail.native-context/v1。origin.native_session=`01a161c7-38d2-7333-a025-9d0be8d6a62a`、turn_id=`01a1668c-0314-7980-8189-ed86da2fecd0`；任务快照`006cb969bd7140299abfeebd526af341`、必需R001/R002/R003及精确目标可见。

只读核对实际库events sequence111的native_context.prepared，session/turn/snapshot与宿主包一致，returned_bytes=9014。新增任务Hook激活缺口由本次实际证据关闭，以前失败保留。Codex main仍委托独立原生Hook，本次memory_status=`provided_by_existing_native_hook_not_observed`、memory_chars=0；不声称同一封装已观察main，不声称替换原生历史。

结合下文双端真实MCP任务包/冷页、Claude完整任务/main组合及Codex原独立main正向证据，A06逐端适配闭环完成。未增加模型调用或再次要求信任。下文为原始验收历史。

2026-10-22，本机 Windows，沿用现有 Codex / Claude 登录与模型设置。该 change 对应 A06/A08；整体持续 TODO 目标仍 active。

## 当前消费者契约

`tests.test_native_client_context` 覆盖双端生成配置经官方 SDK 实际 stdio 读取任务包及冷页、越权参数拒绝、reader 无写权、owner/version/freeze/source 边界、完整外层 UTF-8 预算、记忆一次调用/失败/超时、禁用原样透传、任务专用记忆覆盖撤销、配置保留及重复应用。

初始 RED 是缺少命令与 context.packet。安装合并用例同样先观察缺命令 RED，再实现；fixture 的 Windows read_text 编码随后显式修正为 UTF-8。

当前新增原生适配最初 11 项全部通过，22.832s，无跳过。真实 main 正向召回用例调用安装中的 Hook formatter 与既有共享 runtime 的显式 text 模式，未创建私有服务。测试内的其他记忆命令是可控子进程夹具，用于预算、次数与失败边界；不能把夹具当成真实 main 证据。

随后真实 Claude 会话暴露 additionalContext 文件预览边界。新增当前用例先 RED：13472 UTF-16 units 超过原生 10000 限制；实现第二重上限后同一用例 GREEN，1.260s。完整任务放不下时阻止；仅裁剪可选历史。[官方原生规则](https://code.claude.com/docs/en/hooks#add-context-for-claude)

该修复后最终当前批次 **12 项通过，19.386s，无跳过**。

## 原生客户端与传输证据

本机忽略目录 `.artifact-work/native-client-acceptance-20261022-1240/` 保留 argv、JSONL、stderr、最终输出与解析摘要。公共可审阅入口为 `examples/native_client_acceptance.py`。临时审计脚本只重读已有日志，不新增模型调用。

| 观察 | Codex exec | Claude -p |
|---|---|---|
| 真实原生客户端调用 context.packet | 成功 | 成功，客户端名称映射为 context_packet |
| 精确任务目标在工具响应中 | 成功 | 成功 |
| context.get 冷页工具响应保留 CRLF | 成功 | 成功 |
| 模型最终 JSON 逐字符回显 | 成功 | 失败：CRLF 被归一化为 LF |
| 该次退出 / 耗时 | 0 / 73.25s | 0 / 37.94s |

原始 strict `passed` 没有改弱：Claude 仍为 false。分别保留 wire 与最终生成结果，不能以工具传输正确推断模型永远逐字复制。

Claude 首次原生接线曾因 Windows Hook command 未使用正斜杠而未运行，修正后 native session 与现有 Store 的 `native_context.prepared` 可关联。该审计证明处理器准备输出，不能单独证明模型读到完整内容。此前长提示触发 main hybrid/text 的 18 秒期限，状态明确 timed_out、memory_chars=0，未声称成功组合。

项目会话 `a428948e-0672-4a17-aa04-07a8c519b733` 后来实际返回 main 历史 5166 字、returned_bytes=18163、memory_status=returned，证明原服务组合成功；但超过 Claude 原生字符限制，模型只看到了前 2000 字预览，未见 origin。此结果促成上面的 RED→GREEN 修复，不能当作完整注入通过。

修复后的项目会话 `304505f0-a008-4dfc-9580-853114a21b39` 耗时 27.94s、exit0。真实 Hook 回执：memory_status=returned、memory_chars=3095、memory_truncated=true、returned_bytes=14199。模型从注入中回显的原生会话 ID 与实际 session/回执完全一致，并给出精确任务目标及真实 main 来源 `main/codex/context-rail/codex-checkpoint-2026-10-22-t11-57-06-z-context-rail-selected-batch-verified-with-timestamp-boundary`；Claude 项目完整任务与历史组合已实际观察通过。

## 实际项目布局与开关

本项目已创建独立 ongoing task `self-use-01a161c7`，owner `host-01a161c7` epoch 0，精确快照 `a558284943e241eab297739aca0864f2`。本机 receipt `.context-rail/active-native-client.json` 指向真实 bundle、单一源码 runner、registry 与数据库。合并写入项目 `.codex/config.toml`、`.codex/hooks.json`、`.mcp.json`、`.claude/settings.local.json`，保留其他设置并保存变更前备份。

Codex 原有独立用户记忆 Hook 继续运行，新增处理器用 --native-memory 避免重复调用；Claude 在绑定启用时显式使用 task-only text helper，停用恢复原命令。未改个人模型、凭据、共享服务和监听器。

另一次真实停用验收使用独立 registry 副本：停用 Rail 后原 main 返回历史，无 Rail envelope；同一 Bridge snapshot 仍可完整编译精确目标。结果保存于 `disabled-live-main-summary.json`。源码/配置/不可重建数据库与可重建本机输出位置见 NATIVE_CLIENTS.md；没有复制第二套 Bridge 或后台监听器。

## 构建与限制

`openspec.cmd validate native-client-context --strict` 通过，`git diff --check` 通过。wheel 使用 `uv build --wheel --offline --no-build-isolation --out-dir dist --python C:/Users/wblxr/anaconda3/python.exe` 成功；已有 bdist_conda 入口缺 conda.auxlib.packaging 警告非致命。最后的 Claude 字符上限修复之后已重新构建成功。

没有执行旧批次/全项目回归，也没有运行 smoke、校验和或启动探针。新增文件与现有修改均未提交/推送。A06 只有在两端原生实际接线及必要记忆组合证据齐备后才勾选；用户已经表示完成 Codex 原生审阅，仍以实际调用确认激活，不能直接写信任记录或使用绕过参数。

用户两次报告已信任新增项目 Hook。之后最后一次 Codex 原生 exec（--no-daemon，无绕过参数）会话 `01a163cc-0c2f-7b12-bbec-5fa59a3c124f` 退出0、68.47s，原 main 历史来源可见，但新增任务 Hook 没有 native_context.prepared 回执，模型的 hook_native_session=unavailable。此前只读打开原生 /hooks 看到两项 UserPromptSubmit：旧用户记忆 Trusted，新增项目项 New hook - review required。用户后续确认与本机实际调用仍未吻合，激活保留 not_observed；没有再次请求相同确认、写信任记录或重复付费调用。协议/MCP读取成功与此缺口分别保留。
