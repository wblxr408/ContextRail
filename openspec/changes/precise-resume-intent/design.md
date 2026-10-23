# Design

## Context

动机见proposal.md。Bridge schema1已经验证supersession、逐项revision及接收回执；R snapshot要求当前task version，原handoff冻结源写入并带epoch fencing。原M normalized_events保存完整处理后用户文本。现有TaskNavigation只有指定snapshot的只读读取。

## Goals / Non-Goals

Goals: 显式语义选择、精确原文窗口、候选与领取隔离，真实重送只有一次所有权效果。

Non-Goals: 自动理解所有历史要求、自动选择项目最新snapshot、修改宿主最终请求、自动转移Bridge workspace writer或创建聊天。Rail lease与Bridge filesystem claim继续分别管理。

## Decisions

Bridge账本增加可选resume_intent扩展，三个角色各引用requirement_id与明确UTF8 start/end；原校验器仍验证原账本。扩展由Rail额外验证，完整用户消息保存为冷源，精确quote窗口与START内意图成为必需选择，完整来源清单另有精确冷ref。原生Hook用引用视图避免重复嵌入清单，并收缩可选页满足原生字符上限，必需目标/纠正不裁剪。旧包显示intent未声明，不猜最近一条就是纠正。此选择比前后截断更能定位中间和重复quote。

原M投影是可信host显式只读函数/CLI，从既有事件表取精确用户事件，返回所选窗口、来源和loss。full文本可显式导出到新文件用作Bridge来源，不启动第二监听器。

会话映射和恢复offer/receipt沿用R events，不增加数据库或模型工具写权限。映射保存exact scope/snapshot/task version/native session；发现只返回本scope候选，精确ID缺失不以最新替代。预览使用原Bridge freshness和Compiler预算。

领取由host显式offer驱动原Store.prepare/Compiler.for_handoff；accept需声明该offer已送达并逐条核对要求，依次复用acknowledge/validate/activate。operation ID和参数在原事件事务记录；重试只继续同一handoff，不重领另一候选。原生workspace writer仍须按原Bridge协议独立取得，结果明确说明Rail所有权不代表workspace写权限。

上游ai-memory固定1bd8663的ops.rs::accept_handoff_in_transaction重查source_busy/范围及CAS是适配参照；借鉴显式选择后重新核对，沿用Rail已有冻结/epoch。没有复制其默认SessionStart领取、最新cwd候选、过期清理或Rust连接池。context-mode的恢复快照保持经历/当前请求分层参照，不继承固定截断。无需新依赖或重新选型。

## Risks / Trade-offs

宿主标注的语义关系不等于机器证明所有要求完整 → 显示declared-only和未核对历史；逐条接收仍为host attestation。外部源文件不受SQLite锁保护 → 每次预览/offer/accept与重送重新读取，保证本次观察一致，不宣称跨文件分布式原子性。offer后预算失败可能留下原prepared冻结 → 精确重送继续，或由原owner显式abort，不自动夺锁。

## Migration Plan

仅扩展账本、R events与原host方法，不改变schema10。先冻结整阶段消费者，集中实现后一次验收；实际将本聊天最新纠正导出为新Bridge revision，更新原registry中的base指针并验证相同reader精确回读。保留旧包、原本机数据库备份与所有旧证据；无新Hook处理器或trust步骤。阶段完成后按用户约定提交上传。
