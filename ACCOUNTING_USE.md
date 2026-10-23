# 请求计量、回读统计与收益建议

D07/H03/D08为同一阶段。使用原R UsageLedger/request_usage/events、原M监听器，不启动模型或新observer。计量不会使Rail取得最终请求控制权；只有宿主实际提交的最终序列化bytes或usage才是该来源观测。

## 宿主最终请求

```python
from contextrail.accounting import HostAccounting

meter = HostAccounting(store, scope, packet.target.session)
receipt = meter.record_request(
    packet, observation_id="host-stable-observation-id",
    final_request=final_serialized_bytes,
    components={"system": [0, 100]},
    reserves={"unit": "tokens", "output": 1000, "safety": 200},
    usage=provider_response_usage, usage_format="openai_responses",
    request_id=actual_provider_request_id,
)
```

范围是最终bytes中的不重叠[start,end)，可命名system/tools/history/working_set/other；未归类字节含格式开销。调用方只有知道实际位置才提供范围。final_request未提供时final_request_bytes为null，packet字节只显示为关联包，不能补成最终请求量。计量库只保存数字/标识/来源，不保存请求正文。

宿主可传SDK可信token_counter(bytes)回调和counter_id；未运行真实计数器或没有reported usage时input_tokens为null，bytes不换算token。测试中的fixture counter仅证明回调接线，不证明实际模型分词。RequestBudget现在带unit，必须与Compiler的显式measure/unit一致；预留是计划值，按自己的unit保留，不和观测输入混加。

OpenAI usage的cached是input子集、reasoning是output子集，不重复相加；缺cache-write或其他字段为null。依据[OpenAI usage说明](https://developers.openai.com/api/docs/guides/agents-api/observability)与[输出token计数](https://developers.openai.com/api/docs/guides/token-counting)，2026-10-23实读。这里不进行费用换算。

原request_usage以observation_id在scope/session内幂等；不同计量/packet/usage/身份复用ID拒绝。因不留正文，不验证等长payload是否异内容；这是计量回执身份，不能代替业务执行幂等或请求签名。实际不同请求/attempt使用独立观测ID，provider request_id仍遵守原唯一约束。旧聚合账本与请求账本分别呈现，避免不明重叠和跨unit相加。

## CLI与只读统计

宿主将asdict(Packet)保存为JSON；只读MCP不能提交usage或改变宿主路径。

```powershell
.venv/Scripts/python.exe -m contextrail record-request --workspace D:/ContextRail --task self-use-01a161c7 --session host-01a161c7 --packet packet.json --input observation.json --final-request final-request.bin
.venv/Scripts/python.exe -m contextrail metrics --workspace D:/ContextRail --task self-use-01a161c7 --session host-01a161c7 --query-binding D:/ContextRail/.context-rail/clients/self-use-01a161c7/v1/query-sources.json
```

observation.json包含observation_id、components/reserves，以及可选usage/usage_format/request_id/run_id/attempt/latency_ms。final-request文件是宿主已序列化的实际输入，ledger不保存其内容；本CLI不发API请求。MCP对应context.metrics，可按offset/limit/budget分页；统计本身不再递归计量。

既有MCP adapter记录工具应用结果JSON的returned_bytes、context.get原文窗口readback_bytes和执行该工具的elapsed ms；这些是应用层返回，不是整个JSONRPC wire，也不代表provider输入。capture-result保存raw/returned关联同source_call_id，raw去重计来源体积，重复响应和后续回读分别保留。索引source bytes是存储/处理量，不是节省。SDK显式meter.call_tool可绑定request_observation_id，原MCP不知道provider request ID时保持null。

工具计量沿用原events，MCP重连后仍可读。存储失败不影响原工具结果，当前reader会显示measurement_not_persisted，跨进程未记录范围仍unknown。失败读取的returned/readback不填成功的0。没有成对最终请求对照时provider_input_savings/cost_savings始终null，不用首次小预览减去raw后声称整体收益。

## 原生usage来源

Codex token_count通过原JSONL owner捕获为usage.reported。适配context-mode usage.ts字段解析，但不复制其reasoning重复相加、缺失补0、按行索引第二游标或pricing。last与cumulative分别保存；相同计数的不同物理记录仍是不同来源事件，原生ID不存在时用既有文件generation/offset身份，重放不会新增。

context.metrics读取query binding限定的workspace/精确会话最新200份报告，展示最近已报告cumulative，不逐条求和，不归给未观测的Rail request。info=null保留unknown；无来源保持not_configured/未观测。旧token_count不默认全历史补读。native_reported不是最终账单，本阶段不推断Claude原生usage或其他客户端的私有格式。

## 摘要与缓存建议

```json
{"action":"compaction","trigger":"budget_pressure","unit":"tokens","before_input":1000,"after_input":500,"future_turns":1,"generation_cost":600,"validation_cost":50,"expected_recovery_cost":100,"cache_loss_cost":50,"cache_write_cost":0}
```

交给context.efficiency或CLI efficiency --input proposal.json。所有量使用同一声明unit，成本是显式估计，不是已节省事实；缺任何成本/轮数时insufficient_data，未知不补0。未来净收益=(before-after)*future_turns-生成-校验-回读-缓存损失-写入，严格为正才consider，否则keep_current。break_even_turns给出严格正收益所需最少轮数；复用原CompactionEstimate。

触发仅phase_boundary/source_changed/budget_pressure/recovery_spike/explicit_request，turn_count本身保持原包。结果只读，不生成摘要/缓存，不启动模型。当前调用的回读量可作为下一次成本估计的证据，但不会把历史回读字节自动换算未来token或费用。

宿主可显式使用`CompactionLifecycle.begin_if_efficient(scope, lease, snapshot, proposal)`：未知或非正收益返回kept_current、无compaction记录；正收益只准备原有生命周期，仍不调用模型。`CacheLayoutPolicy.compile_if_efficient(..., proposal=...)`依建议选择原layout或当前普通包，provider_cache_hit保持unknown。二者是主机SDK接线；MCP的efficiency始终只读，原手动begin仍是主机明确操作。

当前阶段契约tests/test_request_accounting_stage.py；结果见openspec/changes/observed-request-accounting/verification.md。
