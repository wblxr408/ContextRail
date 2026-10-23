# Proposal

## Why

当前源包只包含最初的持续实现目标，后来的整阶段验收和上传要求没有进入恢复必需材料。精确快照读取与领取协议也未统一接线，容易在选择候选时误用另一个任务或把预览当成接管。

## What Changes

- 以 Bridge 账本扩展显式声明原始目标、最新请求、最后纠正与下一步；保留原文 UTF8 span 和 supersession，不使用前400字代替完整来源。
- 从原 M 的精确用户事件生成可审阅的 Bridge 来源投影；沿用原采集者，无新增观察者。
- 增加任务/版本/检查点/原生会话绑定的恢复候选与只读预览；缺失和歧义返回候选与覆盖损失，不自动选择最新。
- 显式领取复用原 Rail handoff 的准备、送达、校验、激活；幂等重送返回同一结果，来源变化拒绝。workspace writer 仍由 Bridge 独立协议保护。

## Capabilities

### New Capabilities

- `resume-intent`: 明确来源与替代关系的恢复目标、纠正和原文窗口。
- `precise-resume`: 精确候选、只读预览、会话绑定及显式领取。

### Modified Capabilities

无；主 specs 目录尚无发布能力，复用此前 Bridge/Store 契约，不覆盖其原生所有权语义。

## Impact

影响 Bridge 导入、navigation、原生客户端投影、可信 host SDK 和 CLI。复用 Python/SQLite、原 M normalized_events、原 R events 和 handoff，无新增依赖、模型请求或默认后台。E05/E06/I02作为同一阶段集中验收，完成后提交上传。
