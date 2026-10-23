# Spec Delta

## Purpose

在同一原生会话可能同时收到监听记录、工具Hook及显式资料导入时明确采集入口归属。保持现有JSONL原始证据与知识捕获链为唯一原生owner，Hook回执说明委托而不重复学习，入口标签不能由消息正文伪造。

## ADDED Requirements

### Requirement: One native capture owner
系统 SHALL 为原生workspace/client/session确定唯一capture_owner。JSONL保持原生采集权，可选Hook MUST 只确认委托，不因先到或自报producer而生成第二份事件/知识效果。原有只读召回和启动注入保持可用。

#### Scenario: Hook and listener observe one turn
- **WHEN** 工具Hook与JSONL以任意顺序送达同一用户回合
- **THEN** 只有原owner的采集产生效果，Hook有无正文的委托诊断，重复投递不产生第二个知识job。

### Requirement: Explicit source attribution
JSONL、Hook与显式导入 SHALL 携带可信入口确定的producer/capture_owner及source_event，归属与效果同事务保存。委托回执 MUST 不冒充已成功保存事件，未知原生ID应保持未知。

#### Scenario: Delegation while owner is not observed
- **WHEN** Hook记录委托且没有原owner实际捕获证据
- **THEN** 仅报告策略委托与未观察到投递结果，不用回执伪造捕获成功。
