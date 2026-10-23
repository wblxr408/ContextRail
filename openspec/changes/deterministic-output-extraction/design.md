# Design

## Context

OutputRecorder 已保存 stdout/stderr/manifest，ContextTools 绑定 scope/session 并按 Ref 回读；Store 不需新表。B03 的借鉴来自 context-mode 在执行端过滤大结果再索引的机制，本项目把提取限制在已经保存的证据中。现有 StructuralChunker 只提供块引用，不提供字段解析。

## Goals / Non-Goals

提供可核对的固定提取语义、完整扫描统计和有界结果。任意脚本执行、自动 format 猜测、自由 JSONPath/SQL/正则、模型摘要及目录采集均不进入本批。

## Decisions

1. 标准库实现 EvidenceExtractor，限制输入 16MiB、展示最多100项/结果预算；读取完整既存 revision，输出 derived_extraction_with_source_spans，不保存第二份原文。外部命令仍由宿主执行。
2. JSON 使用 RFC6901 Pointer；用标准 JSONDecoder 解析值并记录原始字符边界，再映射 UTF-8 字节。对象重复键、NaN/Infinity、无效转义拒绝。filter 只允许记录数组内相对 Pointer 与 JSON 标量类型严格相等，不执行表达式。
3. CSV 使用 csv.reader(strict=True) 与 newline=''，按 reader.line_num 映射物理行到精确字节；表头重复/行宽异常拒绝。指定 numeric 列使用 Decimal，有效数/空白/无效数分别计数，sum/min/max/mean 用十进制文本，避免 float 漂移。
4. 日志仅按固定 ERROR/FATAL/CRITICAL/Traceback/Exception 标记选择，聚合相同原始非换行消息，计数全扫描，样本定位原行；不自动归并不同数字/路径而丢失细节。
5. Git 显式 git_status（porcelain v1 -z）或 git_numstat（--numstat -z）。按 NUL 和固定前缀解析；重命名记录消费两个路径，binary numstat 的 '-' 保留 null，不视为0。非 UTF-8 原文保持可回读并报告 parse_failed。
6. API 固定 kind/options，所有参数先校验。预算作用于整个返回 JSON，通过省略尾部样本保持统计，省略不是解析失败。不存在/无权引用保留原错误；语法错误才返回 parse_failed。

## Risks / Trade-offs

大量嵌套 JSON 的索引需要内存 → 深度/节点上限，资源边界单独报告 extraction_incomplete。CSV 非标准方言 → 显式单字符 delimiter，拒绝不规范行。有限字节预算可能容不下大单项 → 完整来源引用保留、标记 omitted，不截断值冒充完整。没有模型推断纠错。

## Evidence

- https://www.rfc-editor.org/rfc/rfc6901 ：Pointer 转义与数组下标。
- https://git-scm.com/docs/git-status ：porcelain v1 -z 的目标/源路径顺序。
- https://git-scm.com/docs/git-diff ：numstat -z、binary 与重命名字段。
- https://github.com/mksglu/context-mode/blob/a92cf6a77ac02971fb5fafe2155e5ee229491884/src/server.ts ：执行后的小结果/索引机制；本批不复制执行器。
