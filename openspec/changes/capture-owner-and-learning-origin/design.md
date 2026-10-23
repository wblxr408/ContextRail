# Design

## Context

原生Hook当前只召回，E01只有JSONL捕获；新事件metadata只有producer，尚无归属认领。学习reduce_record按前缀跳过注入，既可能保留旧user又丢掉块后的纠正。显式import任意md/json均可能重新学习本服务生成资料。

## Goals / Non-Goals

完成F02/F03的唯一入口与学习来源隔离，继续使用原M库/监听器。当前选择固定原生JSONL owner；不是用户请求的多后端切换器，不新增默认工具Hook或原生trust。真正用户回应与原始证据不能因学习过滤而丢失。

## Decisions

1. capture_policy.py在原库增加capture_owners/receipts，原生owner固定existing_jsonl_listener；显式导入为existing_import_queue。可选capture-hook入口只写有界无正文委托回执；入口producer由代码决定，输入自报字段不能覆盖。BEGIN IMMEDIATE/原事务原子认领；委托不意味着owner在线或已保存。
2. learning_origin.py为纯规则投影，沿用Python stdlib及现有栈。识别完整已知schema、原生isMeta/isCompactSummary、受保留格式包裹的注入块、主服务管理的Markdown及保留Bridge目录。完整普通JSON的origin自报和关键词不控制eligibility，代码围栏里的例子不当注入执行。context-mode现有“自有guidance不能当工具错误”作为范围参照，不继承其全局关键词推断。
3. 原始user/final与F01内容绑定不因学习投影改写。job只追加_origin，分类前在内存构造user/final投影；模型、quote校验、自动事实使用同一投影并保留原事件引用。纯历史或控制turn过滤，块外真实回应保留；旧queued/ready_to_save在reserve/save之前重评，已saved事实不追溯抹除。
4. E01原始事件先保存，元数据新增origin/capture_owner及raw source_event；投影不删raw，派生source refs是来源链而非原文位置。Bridge任务包/native wrapper新增来源链，继续使用其现有预算；旧包通过已知形态兼容识别。M生成页与自动事实继承来源元数据，显式导入派生资料仅认领container/记录过滤，不生成part jobs。
5. 本批学习入口规则属于确定性评估TDD：冻结“历史不新学/纠正保留/入口伪造无权/原文仍可读”消费者契约，先RED再GREEN，不调用模型，不跑旧能力/安全回归。固定owner是实际产品选择，Hook开启后主动委托而非第二次捕获。

## Risks / Trade-offs

任意未标记改写的历史无法靠规则辨别→仅声明结构/来源可确认的边界，保留现有语义判别器和手工记忆能力。旧记录缺native provenance→从可确认内容重评，不能声称修复所有历史事实。受保留标签的文字例子→围栏代码保留，块外新回应保留，不用关键词粗过滤。

## Migration Plan

原库增量表，不改旧job ID/F01正文绑定。当前新行为评估通过后备份app和SQLite，用原control.ps1部署/重启；实际历史包及纠正作为明确标注的验收资料，确保不产生无意知识。保留原Hook召回，无新trust步骤；回退只恢复增量源码，保留新审计表和已有事实。
