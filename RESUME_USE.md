# 精确恢复、用户纠正与领取

E05/E06/I02复用原Bridge账本、原M事件与原R handoff。源码位于intent.py和recovery.py；新命令是可信host操作，MCP目录仍只读。

## 将纠正写入恢复源

在原schema1 requirements.json增加可选扩展，三个角色明确引用既有要求及来源UTF8字节范围：

```json
{"resume_intent":{"original_goal":{"requirement_id":"R001","start":0,"end":40},"latest_request":{"requirement_id":"R003","start":16000,"end":16080},"latest_correction":{"requirement_id":"R003","start":16000,"end":16080}}}
```

范围必须逐字等于该要求的source.quote，且不能切开UTF8。数字只是格式示例，实际从所选原文计算；多次出现同quote也由范围区分。原始目标可以superseded，最新请求和最后纠正必须active；没有已标注纠正时latest_correction可显式null。替代关系继续由原Bridge校验器验证。语义角色是host声明，不能证明所有用户历史已核对。

bridge-import会保留原始完整源作为冷页，生成必需START与意图投影，固定指定窗口及当前要求。START显示原始目标、有效请求、纠正和supersession；next_action采用host标注的有效请求statement。预算不足会报错，不省掉纠正。旧包仍可只读，但intent显示not_declared；准备可写恢复前须先声明当前意图。

原生Hook使用意图引用视图，完整来源清单为可精确回读的冷工件，START与原文窗口保持必需。Claude内联还受10000个UTF16 code unit上限约束；处理器减少可选页和重复清单，不裁掉必需要求。新生成的源投影/候选/offer/accept管理输出也交回原learning-origin owner排除自动学习，显式块外新回复仍保留。

从原M取来源可用SDK project_native_user或CLI intent-project，按workspace/client/native-session/event-id限定，start/end针对data.text的处理后UTF8字节。只接受user角色，仍是明确选定的历史数据；不自动把助手或工具内容当用户规则。--output只创建一个新的完整处理后文本文件，用于Bridge source.path；终端返回所选窗口和原事件引用/loss。已有文件拒绝覆盖。source.quote可选长消息中间或末尾，原M保留全来源，没有第二监听器。

```powershell
python -m contextrail intent-project --workspace D:/YourProject --session host --client codex --native-session EXACT_SESSION --event-id EXACT_EVENT --start 16000 --end 16080 --output D:/YourProject/new-bundle/user.md
```

## 只读候选与预览

```powershell
python -m contextrail recover --workspace D:/YourProject --task TASK --session host
python -m contextrail recover --workspace D:/YourProject --task TASK --session host --snapshot EXACT_ID --version 42 --budget 24000
```

无精确ID或version时返回本task的候选，不选择同项目最新；foreign ID不会读取另一task正文。候选新鲜度未核对，preview才核对当前R版本和Bridge原文。缺失/歧义仍需显式选择。只读操作不冻结源、不消耗handoff、不改变epoch。

resume-bind由当前owner显式将client/native-session绑定到snapshot/version/binding-id；同ID同参数重送复用，异参数拒绝。原Hook准备回执也保留精确snapshot/native session映射。recover加--client及--native-session时只匹配这些映射；旧事件页外或未绑定范围为unknown。没有用cwd或项目latest猜测跨会话任务。

## 明确送达后才接受

1. 源端resume-offer指定snapshot/version、--epoch、--operation-id和--target的Target JSON。它调用原prepare冻结该任务，生成原Compiler目标packet，并持久保存issued offer。
2. 宿主把该packet实际交给指定目标后，目标准备receipt JSON，包含offer_id、snapshot、version、target_session、delivered:true、checks。每项check包含必需要求的id/revision、接收方interpretation与next_action。调用resume-accept --operation-id ... --receipt ...。
3. 原acknowledge/validate/activate推进一次epoch；完全相同重送返回原lease，参数或来源变化拒绝。中断后按原phase继续同offer，不另选候选。prepared但还未发出packet的失败可重送同offer；取消使用源端resume-abort与原lease。

SDK入口Recovery.offer/accept与CLI含义相同。送达与语义核对是host/接收方声明，不能称为模型消费证明。只验证选定的精确task与source，不宣称外部文件和SQLite构成原子分布式事务。

Rail lease保护R任务写入；工作目录写入仍须按context-bridge writer.py取得独立claim并确认源端停写。这里不自动转移workspace claim、不创建聊天、不因超时夺锁。预览和receipt都不能替代该所有权要求。
