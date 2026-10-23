# Design

## Context

动机见proposal。M原jobs/source_events/cursors均在state.sqlite，原监听循环唯一拥有原生采集。capture_record先保存工具正文，reduce_record另形成知识候选；两处必须用同一次项目规则。原resource broker有容量与超时，原reserve_call有小时/日预算；都继续保留。

## Goals / Non-Goals

Goals: 策略在所有派生持久化前生效；重送只有一次队列效果；未完成工作不因预算/重启丢失；文件身份变化不串会话。

Non-Goals: 新网络队列或观察者、自动删除旧知识、自动从未知工具/shell脚本猜路径、强杀正在执行的模型请求、保证外部笔记写入exactly-once。

## Decisions

capture_rules作为canonical纯Python规则，R配置复用它，M读取有界.contextrail.toml。full/metadata_only/skip在原M normalize后、source_events与索引前执行；用户文本独立配置。已知文件工具只用明确字段，未知工具/搜索/执行在有路径规则时采用保守元数据；结果继承call-ID决定。受限工具导致该轮assistant派生正文也不再持久化，显式用户文本仍按用户规则。旧已捕获记录不自动改写，重放政策变化以既有身份冲突机制明确展示。

持久delivery inbox置于原M数据库；提交先验证/脱敏与记录稳定ID，逐项drain到原history_import或无正文Hook委托。每项单独提交确认，原导入root+parts身份仍保护job唯一；丢失ack后的重试按原身份补完。失败有原因/available，不删除已确认项或超过次数项。原Service循环每次有限drain，CLI支持精确ID。没有第二原生正文入口。

job_control记录延期/取消审计；预算耗尽不新增attempt/调用，在原jobs.available设下次恢复点。原meta另保存judge/index共享退避期限，避免其他积压依次冲击同一不可用资源，重启也保留。资源失败仍原retry，status显示积压数、最早等待、分原因及下次时间。排队项可直接取消；processing协作请求在原模型检查点停止后续保存，不假装已撤回外部效果。原calls账本、结果检查点和稳定文件命名保持原owner。

stream_cursor扩展原游标，事务内关联物理身份、逻辑epoch与历史代游标。常规追加继续；inode/birth变更、缩短、同尺寸mtime变化重开代并清空会话。on_moved保存原逻辑源与实际新路径的别名，即使新路径不以jsonl结尾也可补读旧代末尾；通知顺序变化时用历史代继续。半行不提交为记录，超长/坏行状态明确，有界跳过过长体直到换行，避免无限阻塞。旧无身份游标采用时明确标注历史未知，不默认全量补读。文件截断后在两次观察之间又长回更大尺寸且身份未变，元数据无法区分于追加，作为明确覆盖限制，不构造hash探针。

上游采用ai-memory固定1bd8663 capture_policy.rs的严格元数据边界与hook_spool.rs逐项durable ack思路；不用其容量/TTL丢弃、脱离进程后台drain或完整Rust服务。原项目Python/SQLite不换型，无新增依赖。

## Risks / Trade-offs

工具外的独立用户原文由显式user_mode决定，文件规则不是所有秘密语义识别器。已存在历史不自动删除。未知/缺失工具关系显示元数据/缺口，不能冒充全文。元数据不能识别未观察到的原地截断后重长；保留该限制并支持显式来源恢复，不能称全场景无损。

## Migration Plan

先冻结整阶段消费者观察RED，再集中实现/验收。新表均在原M库按需建立，部署前通过原control停止并一致备份；仅部署受影响模块再原入口启动。对本项目开启有界策略，真实source/import/queue操作留回执。保留旧schema、原始队列和失败状态；完成模块后提交上传。R工作证据库无须增加schema。
