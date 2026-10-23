# Spec Delta

## Purpose

让自用项目明确选择保留完整采集、只留元数据或排除正文，并让同一决定贯通原生事件、游标与知识候选，避免只过滤队列而在其他派生记录留下原始内容。

## ADDED Requirements

### Requirement: Policy before persistence
系统 SHALL 在事件、索引、游标和知识队列持久化前执行项目full/metadata_only/skip决定。元数据/排除项不得包含被排除正文；状态必须说明规则原因，用户文本采用独立规则。

#### Scenario: Private tool output
- **WHEN** 已知文件工具指向ignore范围，结果及该轮派生助手文本包含其内容
- **THEN** 对应持久化项仅保留政策元数据，显式用户文本仍遵循独立配置

### Requirement: Conservative unsupported input
系统 MUST NOT 从未知工具或任意shell文本猜测文件读取权限；在路径规则有效而无法核对时 SHALL 保留可解释元数据。无效配置不得静默按全文捕获。

#### Scenario: Unsupported tool schema
- **WHEN** 未知工具携带未支持的参数与输出
- **THEN** 保存未知路径覆盖状态，不读取额外文件，也不把其参数/输出当已获路径许可的全文
