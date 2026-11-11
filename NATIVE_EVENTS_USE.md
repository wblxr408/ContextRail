# 可恢复的原生事件证据

## 中断后补读原生历史（E07）

```powershell
contextrail native-history --workspace . --client codex --native-session <exact-native-session>
contextrail native-history --workspace . --client claude --native-session <exact-native-session> --output .context-rail/recovery/selected-session
```

命令可在没有 Rail 数据库或交接包时运行。它从原 Engineering Memory 配置中启用的来源读取，按精确 checkout、客户端和会话校验，返回可见用户请求、final、已完成工具结果与结果未知的调用。来源缺失或有歧义时，宿主可通过 `--source` 指定启用根目录内的确切来源。

分页将返回的 `next_cursor` 作为 `--input` JSON 的 `cursor` 继续。若要读后来追加的记录，提供 `end_cursor` 并设置 `follow=true`。默认以固定文件结束位置分页，部分末行和已知遗漏通过 `gaps` 报告。大条目保留精确 Ref，输入 `{"ref":...,"start":0,"end":4096}` 返回处理版 JSON 的 UTF-8 字节窗口，编码为 base64。游标核对物理文件身份、大小和修改时间。

MCP 的 `context.native_history` 和 `context.native_history_get` 只能读取已绑定的会话，不能通过 Ref 或游标选择有歧义的来源。导出的 `STATE.md` 与 `history.json` 供下一个 Bridge 检查点选用。恢复时核对当前要求，结果未知的动作需要先确认再处理。

E01接入原EngineeringMemoryListener：完整用户记录在没有final时也先持久化；tool call、tool result、assistant文本和lifecycle分别保存。原来的完成回合/meaningful规则继续决定知识候选，事件本身不发模型请求。没有新增观察器或另一套长期记忆库。

Codex使用message/turn/call ID，识别final与final_answer；Claude的user记录中tool_result只产生工具结果，真实文本才是用户请求，message.id/uuid/promptId分别保留。thinking/reasoning不采集。未知工具错误状态保持unknown，不因为输出含Error一词就判失败。原始工具名与context-mode的31项名称映射均保留；ELv2移植及修改说明见[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

发生时间只接受明确有时区的来源ISO时间；缺失、无时区或无效时occurred_at为null，source_time_state说明原因。observed_at始终是独立接收时间。列表按capture sequence分页，不冒称真实时间顺序。

证据在F04策略处理后保存。data保留可采集的完整文本/参数/结果，图片等块以omitted_blocks及partial状态标记；超过处理边界会保存omitted状态。它不是所有原始字节的镜像。事件的持久化处理版不依赖源日志后续仍存在；source的JSONL路径/代际/行区间只作出处，不是假装永久有效的原文字节引用。

查询本机已有事件（使用真实native session，不是Rail owner/reader ID）：

```powershell
$py='C:/Users/wblxr/AppData/Roaming/uv/tools/basic-memory/Scripts/python.exe'
$app='C:/Users/wblxr/.codex/services/engineering-memory/app/listener.py'
& $py $app stream --workspace D:/ContextRail --client codex --native-session 01a161c7-38d2-7333-a025-9d0be8d6a62a --role user --limit 10
# 把返回的id填入；较大证据用start/end缩小范围，单位为处理版UTF8 bytes。
& $py $app stream-get --workspace D:/ContextRail --client codex --native-session 01a161c7-38d2-7333-a025-9d0be8d6a62a --event-id '返回的source-event-v1:…' --start 0 --end 1024
```

小范围跨UTF8字符时以base64返回，encoding会明确说明。默认每次原文回读最多65536 bytes。stream默认8192字节响应、20项，最高100项，next_offset用于继续。

显式把原M事件导入当前Rail任务：

```powershell
python -m contextrail event-import --workspace D:/ContextRail --task self-use-01a161c7 --session host-01a161c7 --epoch 0 --client codex --native-session 01a161c7-38d2-7333-a025-9d0be8d6a62a --limit 20
```

来源workspace绑定当前实体工作区，native session/client是可信CLI的明确选择。可用memory-home选择已有M库；这不会创建新服务。每个事件返回body_ref与manifest_ref，现有context.get/search可以回读；同事件重试不增revision。有界stage最多16MiB，输出预算或页数不足时按next_offset继续。发布遵守原owner/epoch/freeze，body与manifest同事务；失败不会只留下半份。

新增证据使旧Rail快照变旧，应沿用现有runtime refresh更新当前视图；它不修改原Bridge要求和base。旧客户端日志已越过游标的部分不会自动声称已补齐，历史只在明确选择后补读。升级继承旧cursor的session/workspace，但不猜测旧turn是否仍打开。F02/F03/F08以及E08/C06仍有独立工作。
