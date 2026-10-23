# Proposal

## Why

当前可选材料按整篇尝试装入，小预算下长材料可能遮蔽其余入口；恢复时也难以把未加载正文、未导入与未知历史区分。D01/D05/H02合为一个较大阶段，使接收者看得见覆盖范围，并能追到要求、证据版本和处理步骤。

## What Changes

- 复用ai-memory固定版本excerpt.rs/brief_tiers.rs，将覆盖优先L0/L1/L2适配到Compiler，整组正文加载或留冷，保留精确回读与目录分页。
- 引入逐来源、有单位和原因的loss报告，连接Bridge packet/STATE导出、原生注入和既有显式导入结果。
- 增加只读MCP/CLI简报和加载解释，连接E08时间线、要求ID与原动作状态；只说明本次生成及已观察回执，不声称模型已消费。
- 冻结整阶段契约后集中实现，再统一验收，只有具体失败定点修复。

## Capabilities

### New Capabilities

- `layered-brief`: 精确快照的覆盖优先可选简报及回读。
- `recovery-loss`: 逐来源覆盖缺口、导入结果和STATE可携带报告。
- `load-explanation`: 无模型的加载理由、来源/版本与经历/动作追踪。

### Modified Capabilities

无已归档主spec；既有Compiler默认v1/v2接口保持，新增显式层级简报路径。

## Impact

Python/SQLite沿用，无新运行时/模型/后台。涉及Compiler、Bridge、host/CLI、native_clients、directory_memory/event_bridge，以及上游MIT移植件和文档。只新增主机显式STATE文件导出，不改旧不可变bundle。
