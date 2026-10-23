# 分层简报、遗漏与加载解释

D01/D05/H02作为一个阶段。新入口基于显式当前snapshot，不选择最近任务或替换Bridge权威。源码入口为Compiler.brief、MCP context.brief/context.explain、CLI brief/explain；Compiler.compile和continue沿用既有行为。

## 生成与回读

```json
{"snapshot":"<显式当前快照>","budget":16000,"offset":0,"limit":10}
```

将上述参数交给context.brief或context.explain；两者生成同一种精确视图，explain包含的加载解释不冒充历史模型输入。必需项和requested完整装入，预算不足明确失败；optional先目录、再L0（最长256 UTF8 bytes）、L1（最长500 bytes），最后有余量才整篇L2。短述是确定性摘录，不能代替字面细节回读。

每层保留同一ref与priority/groups/reason。context.get按ref读原文；大于reader上限时按UTF8边界分段。可选二进制或超过1MiB展开上限保留目录和原因，原件仍可分段回读。重叠分组先合并依赖闭包；L2完整加载闭包或留冷，不能只读半组。跨目录页的组在可选状态下不晋升L2；显式requested会带入全部组员。

目录通过navigation.next_offset继续context.brief，或使用context.list枚举声明refs。全部JSON（包括loss和解释）共同计量；目录也装不下时缩短本页并保留总数/游标，固定元数据与必需证据装不下则报错。默认每页20、最大100。trace_offset/trace_limit为动作/步骤/最近准备回执的独立页，默认5、最大20。

## 逐来源遗漏

loss各项独立标source/unit/scope，不能把refs、bytes、files、events相加：

- total/selected/omitted：范围内总量、完整内容或成功接受单位、未完整加载单位；已知时selected+omitted=total。
- truncated/unsupported：omitted中已观察到的短述或不支持项，可能重叠，不额外相加。unknown是未检查或未观测范围，null表示数量未知。
- reasons/recover：原因与补读/重试入口。完整快照refs已知，不代表完整历史已捕获；main/native历史在此简报未查询时仍unknown。

Bridge会核对源bundle与账本当前性，显示required_ids、每条active requirement的revision/ref/是否加载/留冷原因；外scope要求即使未装入仍计入缺口。Bridge默认packet也有loss，但旧整篇编译器不能分类的冷页原因明确unknown。显式Bridge.packet(layered=True)采用同一分层规则。

原生Hook任务包携带task loss。独立main Hook的正文未被Rail观察时，memory total/selected/omitted保持null；组合Hook实际返回的历史才按UTF8 bytes计算。正文被截短时保留已观察总量与截短状态，但仍不是完整main库覆盖率。native_context.prepared只记准备回执、实际选中ref和loss，不能证明模型消费。

目录→原M导入的memory_import以files计当前页接受、不支持和页外未知；import_receipts单独解释回执展示省略。queued数量是job增量，重送可能为0，knowledge_saved=unknown_not_observed。事件导入native_import以所选会话events计当前提交页，不把导入索引当成完整恢复。

## 加载解释和STATE

explanation展示本次生成的要求/选材，动作读取原actions表，步骤读取原events，timeline给出context.timeline继续入口；最近prepared有原sequence/at/snapshot/ref页及总数。动作/步骤缩短时保留总数与下一页。调用不发main recall或模型请求。动作状态是原Store记录，不能据此保证外部操作exactly-once。

```powershell
$snapshot = (Get-Content D:/ContextRail/.context-rail/active-native-client.json -Raw | ConvertFrom-Json).current_snapshot
.venv/Scripts/python.exe -m contextrail brief --workspace D:/ContextRail --task self-use-01a161c7 --session host-01a161c7 --snapshot $snapshot --budget 16000 --state-output D:/ContextRail/.context-rail/recovery-stage-STATE.md
```

STATE输出为显式新文件，已有文件拒绝覆盖，父目录须已存在。它包含本次完整有界视图、loss、要求和精确refs，可由下一次Bridge handoff的STATE/INDEX引用；旧不可变bundle不反写。预算是本视图JSON单位，不是最终模型请求token。使用Compiler自定义measure时unit沿用声明的真实计量器。

新简报使用既有origin标为context_brief，STATE带context_state_projection来源头；原F03学习投影认识这两个生成类型。再次显式导入会保留完整已处理来源、写history_only回执，新增知识job为0。实际新版STATE为`.context-rail/layered-recovery-5cb54ae0568b4cce9000845fc2b1f5a0-origin-v1-STATE.md`；同快照旧STATE保留为当时产物。

本阶段契约为tests/test_layered_recovery_stage.py，实际结果在openspec/changes/layered-recovery-explanation/verification.md。上游固定来源与差异见THIRD_PARTY_NOTICES.md。
