# Spec Delta

## Purpose

在有限恢复预算中先保留多份可选材料的可发现入口，再扩展细节，使长材料不会独占全部预算；每层保留相同证据版本、信任边界和原文读取方式。

## ADDED Requirements

### Requirement: Coverage before depth
显式分层简报 SHALL 先保留可选材料目录或L0，再依余量扩展L1/L2；必需和显式请求内容 MUST 完整加载，不能静默降级。

#### Scenario: Long optional page competes with shorter pages
- **WHEN** 多篇可选材料竞争有限预算
- **THEN** 先提供可容纳的短述入口，再扩展完整正文；未展示入口可分页发现

### Requirement: Exact atomic evidence
简报 SHALL 保留精确ref，L2整组加载或留冷，重叠分组按连通闭包处理；无效必需来源、旧快照或越界请求 MUST 拒绝。

#### Scenario: Requested member belongs to overlapping groups
- **WHEN** 请求组内一个成员且依赖组重叠
- **THEN** 完整加载闭包或明确预算失败，不能只返回一部分

#### Scenario: Binary optional evidence
- **WHEN** 可选证据不是可注入文本
- **THEN** 展示目录、原因与二进制回读ref，不能假装已经读入正文
