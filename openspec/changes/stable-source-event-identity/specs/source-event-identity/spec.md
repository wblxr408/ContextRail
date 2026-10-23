# Spec Delta

## Purpose

为现有记忆队列及后续任务事件桥提供可追溯的来源身份，使事件重送、流式记录追加和历史补录能复用同一采集效果，同时保留内容变化、旧数据歧义和缺少原生身份时的可见边界。

## ADDED Requirements

### Requirement: Source coordinates define identity
系统 SHALL 以明确命名空间、workspace、原生session及event/turn/message ID界定原生事件身份，正文 MUST 不参与该身份；原生身份不足时 SHALL 标记文件代际与记录位置形成的弱身份。

#### Scenario: Equal text from separate events
- **WHEN** 两个不同原生事件或无原生ID的不同记录具有相同正文
- **THEN** 保留两个来源身份及各自一次采集效果，不以相同正文合并。

### Requirement: Atomic replay and conflict handling
来源键认领、队列写入与读取游标 SHALL 原子提交。重送相同事件和内容 MUST 不生成第二个job；同一身份异内容 MUST 保留原job并记录有来源和差异字段的冲突，不能静默覆盖或当新事实采集。

#### Scenario: Changed redelivery
- **WHEN** 已认领事件以不同回复重送
- **THEN** 原采集效果保持一次，并可从状态入口看到该身份的内容冲突。

### Requirement: Preserve legacy queue references
系统 SHALL 保留旧队列ID、状态和已保存事实引用，以兼容映射关联稳定来源身份。旧数据出现多个可能job时 MUST 明确歧义，不任意择一或新增重复采集。

#### Scenario: Replay after upgrade
- **WHEN** 已有旧格式job的原事件被历史补录重送
- **THEN** 关联原job并保持原状态与引用；无法唯一关联时记录歧义供诊断。

### Requirement: Bind imported revisions before chunking
显式文档及Git导入 SHALL 将选定来源和源修订绑定完整处理后内容，再产生分片身份。同修订异内容重送 MUST 在提交分片前报告冲突，不能因分片数量增加或减少静默改变来源含义。

#### Scenario: Imported document changes under same revision
- **WHEN** 同一显式修订以更多、更少或不同分片重送
- **THEN** 原job集合保持，导入返回固定冲突错误并保留可查询诊断。
