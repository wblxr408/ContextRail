# Design

## Context

既有listener完成回合后jid=SHA(kind+session+marker+final)，缺ID时同文丢事件，异内容同ID则多入队。history_import已用event_key+分片序号，但没有绑定整份源修订。retain会拒绝同ID异内容，不能改已有job ID。F04源码和部署入口已在integrations/engineering_memory，必须以此为基线。

## Goals / Non-Goals

建立可给Rail事件桥复用的稳定身份契约、原M库原子认领和兼容诊断。仅采集现有completed-turn/import范围；F02/F08/E01仍有独立工作。没有原生身份的来源copy/原地重写不能冒称全局exactly-once。

## Decisions

1. 复用本地ai-memory 1bd8663（MIT）ops.rs的来源键认领/事件原子写入方法；保留归属验证后才变更键的顺序，不沿用其TTL重用来源键。context-mode 90af14e的insertEvent仍以type+data_hash近期窗口去重，本项不复制该策略。Python/SQLite原栈继续，source_events.py为共享契约并部署为独立event_identity.py。
2. 原生ID规范JSON coordinates包含namespace/workspace/session/event；Codex取turn_id，Claude取message.id（uuid后备）。UUIDv5只编码coordinates，回复正文不进入身份。缺session或marker用canonical source+fstat文件代际+行起点，basis明确为source_position。workspace优先原canonical_path，已移除历史workspace用声明路径归一并标记不确定性。
3. 原state.sqlite新增source_events、source_event_aliases和source_event_conflicts，不另建store。BEGIN IMMEDIATE串行化认领；原子提交queue与cursor。payload._event保留稳定id/coordinates/basis，正文语义比较使用user/final/kind及规范workspace，排除传输路径/offset、处理标记和补录时生成的接收时间。缺来源时间仍是E08待改边界。
4. 延迟迁移：按旧jid或同来源/marker（无marker需同offset）寻找旧job，用处理后内容比较而不重写原job。单一旧job映射原id；旧同身份多个job记ambiguous并列别名，不改变它们的已执行效果。不同内容重送保留原绑定并记录sanitized内容差异；本地只读状态展示有界metadata/字段名，不回显大段正文。
5. 文档修订有container身份，先绑定完整处理文本再给每段生成part身份；同修订变更时拒绝整组。旧分片通过原import jid映射。组内失败回滚新分片与container，另行提交冲突审计后返回固定错误。改动不重新分类旧job或改写已有事实ID。

## Risks / Trade-offs

旧身份已造成的重复学习无法自动撤销→保留所有旧引用及歧义报告。历史workspace已不存在/缺原生ID→声明较弱来源定位，不假装跨搬迁去重。完整内容比较增加原队列本机空间→受F04输入限制，不继承上游丢事件和短TTL。原地同长度文件改写/完整轮换仍属F08，不将本项位置身份误报为轮换完成。

## Migration Plan

以真实临时jobs/cursors取得本批RED/GREEN，验证旧队列升级后再重放、并发重送、内容冲突与事务回滚。新增表由原Store幂等创建，旧job保持原id。沿用既有control.ps1停止后备份app受影响文件与SQLite一致快照，再部署增量并启动；本机使用明确的本批事件输入经真实installed入口确认重放效果。旧二进制不会使用新表，回退源码时保留表和数据库，不做破坏性降级。
