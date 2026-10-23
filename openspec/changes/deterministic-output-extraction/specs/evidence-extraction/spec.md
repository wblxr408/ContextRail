# Spec Delta

## Purpose

让宿主和模型对已经保存的日志、JSON、CSV、Git 输出进行确定性提取，保留每条样本的精确版本与原文字节定位。统计、过滤、省略和解析失败必须显式，读者可以据此回到完整证据核对。

## ADDED Requirements

### Requirement: Deterministic source linked extraction
系统 SHALL 为日志错误聚合、JSON Pointer 与记录相等筛选、CSV 数值统计、Git 稳定格式字段提供只读提取。结果 MUST 携带精确 revision 和原文跨度，声明扫描范围及过滤条件，不改任务版本。

#### Scenario: Multiline CSV and JSON records
- **WHEN** 输入含中文、CRLF、CSV 引号内换行或 JSON 转义路径
- **THEN** 统计及筛选正确，来源引用可回读对应原始记录或值，不从重新序列化文本推算跨度。

#### Scenario: Git renamed paths
- **WHEN** Git -z 输出包含重命名以及空格、制表符或换行文件名
- **THEN** 字段保持原值，记录跨度包含完整改名前后信息，不按空白拆分文件名。

### Requirement: Explicit extraction failure and omission
系统 SHALL 将非法格式、无匹配和输出省略区分；解析失败 MUST 返回原文引用与未完成状态，不能以空结果冒充成功。完整输出 MUST 满足 UTF-8 字节预算，省略结果不得改变完整扫描的统计。

#### Scenario: Malformed input
- **WHEN** 输入 JSON、CSV、Git 记录不完整或含歧义字段
- **THEN** 返回 parse_failed 与精确原文引用，不改已保存证据，仍能按原引用回读。

#### Scenario: Tight result budget
- **WHEN** 完整扫描产生很多匹配但展示预算有限
- **THEN** 统计保持完整，展示条目和省略数明确，结果为完整有效 JSON；连元数据也装不下时明确 budget_exceeded。

### Requirement: Bound host tools and CLI
系统 SHALL 将提取接入现有绑定任务的只读 MCP 与 CLI；输入 MUST 限制为既存工件引用、固定提取类型和受限选项，不接受 scope、文件路径、命令或可执行表达式。

#### Scenario: Actual saved tool output
- **WHEN** 宿主提交原始输出后通过 CLI 或真实 MCP 提取
- **THEN** 读取同一证据版本并能按样本引用回读；越权覆盖拒绝，不执行输入数据中的指令。
