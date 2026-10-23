# Design

## Context

现有 BridgeEvidence 可复核源、原子导出、编译必需要求；MCP 仅原文工具；记忆服务 memory_cli.py hook 返回 UserPromptSubmit.additionalContext。当前 Codex 用户 hooks.json 有记忆命令，Claude 用户 settings.json 没有 hooks。原生 Codex 新/变更 Hook 必须 /hooks 审阅，不手写 trust 状态或使用跳过信任参数。

## Goals / Non-Goals

Goals：A06 的双端任务包/冷页与字段适配，以及 A08 单一源码、显式绑定和停用透传。Non-Goals：替换模型历史、自动发布、第二记忆服务、隐藏推理读取、绕过原生信任或凭一端成功推断另一端。

## Decisions

1. Store 新增 owner-fenced register_reader，只增加现有会话成员，不变更 owner/epoch/version。MCP context.packet 只接受 snapshot/budget，scope/session 已绑定；默认和最大预算明确限制，返回既有 Bridge packet。
2. client-prepare 生成一个入口 runner 指向实体源码，registry.json 保存明确 workspace/task/store/project、两端 reader 和 owner lease、精确 base snapshot；Codex 与 Claude 各自配置引用同一 runner。来源配置与路径不从用户 prompt/transcript 正文推断，多个匹配绑定拒绝。
3. UserPromptSubmit 仅归一化 session_id/cwd/prompt/hook_event_name 与可选 turn_id，缺失 turn 保持 unknown。客户端元数据不赋予写权。payload cwd 必须匹配可信绑定，不读取 transcript_path，不采隐藏推理或声称全事件捕获。
4. Hook 包装器先调用可信配置的原 memory command 一次，再注入完整任务工作集与有来源标签的历史。已有独立原生记忆处理器的客户端用显式 --native-memory 委托，避免重复调用，状态为未由此包装器观察。可选历史按完整外层 JSON UTF-8 bytes 缩略并声明；Claude 另按原生 10000 UTF-16 units 上限保证完整 inline，避免仅交付前 2000 字文件预览。任务要求无法完整装下或来源 stale 时明确阻止，不能只给历史继续。记忆失败/超时可见，任务仍可用；关闭 Rail 时原记忆输出原样透传。
5. refresh 从显式 base snapshot 继承选项，核对同一当前 Bridge 导出与磁盘源，owner lease/freeze 校验后创建当前 task version 的快照；缓存同 base/version 的视图，事件记录 base/source/derived snapshot，不猜另一任务或更新 Bridge。读取 session 可消费，不能凭模型工具刷新。
6. 新配置保持现有 memory/MCP 信息，生成供审阅片段，不覆盖用户其他设置。安装新 Hook 后需原生审阅就明确等待该环节；不修改 trust 值或使用 bypass。实际客户端用当前配置模型和隔离自用夹具验收，不暴露凭据、不改模型、不注册新 daemon。

## Risks / Trade-offs

Codex Hook 信任可能需要用户 UI；模型渠道可能失败。先完成可审阅实现与协议行为，保留两端分别的实际结果，不能提前勾选 A06。源码 runner 依赖本机源码位置与既有解释器；A08 文档明确迁移/升级只更新绑定，不复制 Bridge/记忆后台。记忆可选，始终不反向覆盖当前硬要求。

## Evidence

- https://learn.chatgpt.com/docs/hooks ：UserPromptSubmit JSON、字段及原生信任审阅。
- https://learn.chatgpt.com/docs/extend/mcp?surface=cli ：stdio MCP 配置。
- https://code.claude.com/docs/en/hooks ：原生事件及 additionalContext。
- https://code.claude.com/docs/en/mcp ：Claude stdio 配置。
- 本机 codex exec/claude --help：当前 CLI 接口，不使用更换模型或跳过信任选项。
