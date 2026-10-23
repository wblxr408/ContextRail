# Spec Delta

## Purpose

在长任务恢复时同时保留原始目标、当前有效请求和最后纠正的可回读来源，使消息末尾的新交付要求进入必需上下文，并明确哪些历史或未声明部分尚未核对。

## ADDED Requirements

### Requirement: Explicit effective intent
系统 SHALL 从宿主显式标注的要求ID与原文窗口构造恢复目标。原始目标可已被替代，最新请求和最后纠正 MUST 仍有效；窗口必须对应账本quote，歧义和UTF8边界错误必须拒绝。

#### Scenario: Late delivery correction
- **WHEN** 用户长消息末尾把报告改为源码模块上传，宿主标注最新要求及该末尾窗口
- **THEN** 恢复材料的有效请求与第一步使用新交付要求，保留原始目标及替代关系，窗口可精确回读

### Requirement: Required anchors and honest coverage
系统 SHALL 将声明的目标、纠正、START及来源窗口作为必需材料；预算不足必须报错，未声明意图的旧包保留明确未知，不补造覆盖全部用户历史的结论。

#### Scenario: Insufficient budget
- **WHEN** 必需纠正与导航超过预算
- **THEN** 拒绝生成残缺的当前恢复包

### Requirement: Original event projection
可信宿主 SHALL 可从原记忆服务的精确workspace/client/session/event导出选定用户文本与窗口。助手或工具事件不得自动成为当前用户要求；投影保留原事件引用和处理后字节口径，无新增采集者。

#### Scenario: Select a native tail
- **WHEN** 导出一个完整已捕获的长用户事件末尾窗口
- **THEN** 结果包含实际末尾文本、全来源引用与未选范围，错会话或非用户来源被拒绝
