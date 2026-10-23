# Spec Delta

## Purpose

通过真实 MCP stdio 协议向客户端提供指定任务证据的读取、搜索和分页。宿主能力报告明确读取与 Hook、改写、请求控制的差异，避免工具可用被误称为控制了历史。

## ADDED Requirements

### Requirement: Bound read only MCP access

系统 SHALL 支持 MCP 初始化、工具列表和调用，固定 scope/session 由可信宿主提供；模型参数 MUST 不得覆盖 scope、执行命令或修改任务。

#### Scenario: Read stored tool output
- **WHEN** MCP 客户端初始化后按引用调用读取工具
- **THEN** 返回指定 revision 的真实输出；非法额外参数被拒绝且后续合法读取仍可成功。

### Requirement: Evidence based capability reporting

系统 SHALL 按能力分别报告 supported/unsupported/unknown 及依据；客户端名称或版本字符串本身 MUST 不足以证明 Hook 或最终请求控制。

#### Scenario: Read tools without hooks
- **WHEN** 只绑定读取工具而未提供宿主 Hook 证据
- **THEN** 读取可用，捕获/压缩/改写/最终请求控制为 unknown，并给出不替换历史的降级结果。
