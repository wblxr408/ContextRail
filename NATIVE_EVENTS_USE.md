# 可恢复的原生事件证据

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
