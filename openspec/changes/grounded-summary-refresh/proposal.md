# Proposal

## Why

摘要引用存在不代表事实完整；当前知识页刷新还会覆盖旧版，源变更后可能继续返回过期正文。自用恢复需要找回被摘要省略的字面细节，并在提交前阻止遗漏最后限制或改写版本。

## What Changes

- D03：从已经授权、脱敏的来源抽取带 UTF-8 span 和上下文的路径、命令、URL、错误码、版本、数值单位和否定；R 摘要可搜索/回读，M synthesis 输入保留截断处的细节。
- D04：原 compaction 提交和 M reflect 通过确定性事实覆盖门；强制要求与关键版本/数值/否定核对，推断/未知单独声明，拒绝草稿及原因持久保存。
- D06：知识页绑定证据版本，先保存不可变新版本再切当前；失败保留旧版但不作为当前正文，来源在生成中变化拒绝提交；历史可显式读取。

## Capabilities

### New Capabilities

- `grounded-derivation`: 字面证据、提交前覆盖校验和有版本的派生材料生命周期。

### Modified Capabilities

无；主 spec inventory 当前为空，复用既有 compaction/page 实现而不引入第二套宿主。

## Impact

Python/SQLite 既有栈；contextrail compaction/store 与原 M runtime、MCP page 接线及部署。保持原任务归属、预算、模型和默认观察者。直接借鉴固定 MIT ai-memory keep_tokens/projection 的有界字面保留，增加中文/Windows/span 语义；不复制其服务或模型配置。
