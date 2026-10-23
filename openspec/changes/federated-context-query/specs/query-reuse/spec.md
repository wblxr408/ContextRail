# Spec Delta

## Purpose

在统一检索中减少完全相同的重复查询及重复段落，说明已查过哪些范围和剩余响应预算。重用必须依赖可验证的来源版本，用户提供新线索、要求刷新或回读原文时仍能继续，不设置固定次数封禁。

## ADDED Requirements

### Requirement: Reuse follows source revisions
系统 SHALL 合并同批相同查询，并仅在来源版本相同且参数相同时复用先前结果。不可验证版本或失败来源 MUST不被作为新鲜成功缓存。

#### Scenario: New source arrives
- **WHEN** 同一查询的来源版本改变
- **THEN** 重新查询该源，不能用旧的空结果遮蔽新材料。

### Requirement: Repetition preserves continuation
重复结果 SHALL 标已展示、已查范围与剩余预算，允许刷新、新线索与精确原文回读，不重复灌入同一预览段落。

#### Scenario: User explicitly needs the same evidence
- **WHEN** 用户要求刷新或通过返回ref回读
- **THEN** 操作继续可用，不因先前查询次数被拒绝。
