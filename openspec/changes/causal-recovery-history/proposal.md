# Proposal

## Why

E02 记录现场，但失败后的处理与验证仍靠人工翻日志；没有交接包时，需要直接找回指定原生会话的可见历史。E04、E07 合为一次恢复模块交付。

## What Changes

- 在原 Rail 库保存来源绑定的失败、处理声明与实际验证结果；按错误类别检索，相邻成功保持无关联。
- 直接适配 ai-memory 的只读 transcript 范围导出机制，使用现有 Codex/Claude 归一化和采集策略；提供精确会话、分页游标、原文回读和 Bridge 恢复材料导出。
- 模型只能读取宿主绑定的会话；原始日志、M 游标和当前目标不被恢复读操作修改。

## Capabilities

### New Capabilities

- `causal-recovery-history`: 来源明确的错误处理经历与中断恢复历史。

### Modified Capabilities

无。

## Impact

沿用 Python/SQLite、原 R task_state 与原 M 监听器，增加本地只读适配和宿主 CLI/模型读取入口。上游源码已下载，固定版本与许可沿用 THIRD_PARTY_NOTICES。云端 CLI 未列出已发布环境，本模块实际本机执行，保留个人未提交研究改动。
