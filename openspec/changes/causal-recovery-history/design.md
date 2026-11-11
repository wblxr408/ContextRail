# Design

## Context

WorkScene 已配对原生 session/call 和完整宿主结果。M 原 normalize_record、project_item、sanitize_capture、learning_origin 共同定义可见文本边界。ai-memory 1bd8663 transcript.rs 的 export_transcript_range、locate_session_file 提供精确会话/checkout 和固定结束范围；context-mode 90af14e extractError 只生成错误条目，不能证明修复因果。

## Decisions

1. 新错误经历属于原 R 库派生表。每个完整宿主/原生结果按 session/call 唯一记录，失败用已有 ELv2 classify_error 分类。原结果 Ref 保留全文；摘要有界。明确的处理声明绑定尚未出现的验证 call；实际结果与声明同一 session，状态为 awaiting/failed/unknown/verification_passed。成功只证明该验证通过，文件或命令相似仅供关联候选，不能自动消除失败。
2. 原生补读由宿主指定 memory_home/workspace/client/session，源须在原 M 已启用根目录内，明确 source 可消歧。无监听捕获时可按精确 session 文件名发现；多源不猜最新。只读二进制 JSONL，固定 EOF、文件物理身份和字节游标，逐行复用已有归一化/策略/脱敏，跳过隐藏推理和派生注入。前缀重放建立真实 session/checkout 与工具策略，不信任游标内伪造的上下文。扫描/行/响应均有界，缺失、换代、未结束行显式报告。
3. 返回最后可见请求、最后 final、未匹配调用提示和已完成结果；超预算的单条保留精确处理后 ref，get 重新验证源再读。默认回看尾窗，早期上下文仍用于身份与策略。工具返回只反映来源，不能证明业务完成。
4. CLI 补读在打开 R store 之前执行，崩溃无任务库也可用。MCP 仅可选择 QueryBinding 已固定的 client/session；不接受模型源路径/工作区覆盖。Bridge 显式导出 managed STATE.md 和 history.json，作为带缺口的恢复材料，不自动领取任务或替代当前用户要求。
5. 无新依赖/后台。只移植上游范围读取和选择方法，保留 MIT 来源说明，不复制 Rust 服务、hash 比对或自动最新会话机制。

## Validation

本模块一组消费者先 RED 再 GREEN：相邻成功、明确验证、跨会话拒绝、重放、超时未知、类别检索；Codex/Claude 实际格式、断尾、精确 checkout、凭据/隐藏推理/策略排除、分页与源替换、CLI 无库恢复和绑定模型读。随后 Windows 实际会话只读回看并导出，记录真实缺口。按大阶段验收与上传。
