# Tasks

## 1. 单次结果检索
- [x] 1.1 当前消费者先观察capture --intent缺入口RED，实现限定manifest两流的检索/覆盖与相关标题预览；当前用例核对中部错误、元数据/其他调用隔离、首次未命中后换词及历史ref。
- [x] 1.2 实现最低预算保存前拒绝、完整投影省略和检索降级；当前用例核对执行失败不软化、换intent重送只产生一次业务结果。

## 2. 继续入口与交付
- [x] 2.1 只读MCP context.result_search和CLI result-search接通，当前真实stdio用例证明换查询→原文回读，非法manifest/覆盖拒绝。
- [x] 2.2 完成指南与实际证据，构建/strict/diff通过后勾选B04，保持整体目标active。
