# Design

## Context

indexing.py已有FTS候选、8KiB结构跨度、精确原文字节；result_retrieval.py重复做regex首命中/字符裁剪。StructuralChunker保留Markdown标题和Python符号path，相同标题可有不同ref。Store.get验证原版本完整性。沿用schema8和lexical-spans-v2，不重建索引。

## Goals / Non-Goals

窗口是读取投影，不是摘要或选择计划。原hit.ref保持结构跨度；新增windows和snippet_ref，预览不再假装等于结构全文。窗口不加入依赖，SelectionGroup保持原宿主责任。

## Decisions

1. 单一纯窗口模块按现有_TERM词法和中文bigram定位，保留原字符到UTF8字节映射。名称/路径匹配而正文无词位时明确source_label，不用子串伪造词位。token casefold仍用原词的完整字符区间。
2. 每段按命中顺序生成context_chars默认80（0..256）的窗口，重叠/相邻且总字节不超过2048才合并；不能截断命中本身。默认展示3窗（1..8），每窗至多32词位，返回总数和省略计数。超长输入token沿用FTS上限；返回的全局词位是完整原token或中文bigram。
3. 以(name,revision,byte start/end)保留来源身份，不按标题去重。每个窗口行号为全工件1起始、包含端点的行范围；CRLF按一个LF换行计数，CR-only亦作为行分隔，字节ref仍不改原文。行尾区间归属最后包含的实际字符，空文件为行1。
4. 单次检索缓存最近一个完整来源（至多既有索引输入16MiB），避免同流候选反复读取；不把100个最大来源同时驻留。窗口文本只解码原8KiB块，行号用原完整bytes计算。标题优先结构path，普通段落/流用命中所在行截取并标title_truncated。
5. 共享indexing投影被result_search直接复用，保留stream、execution_status和原候选分页。CLI/MCP可选context_chars/window_limit，默认兼容旧调用；JSON按完整hit裁减，不裁断词位/文本，候选未展示仍可同offset加预算或结构ref回读。

## Risks / Trade-offs

窗口以现有索引跨度为边界，跨块词仍受C03既有词法边界限制；窗口不是全文件语义排序。大量词位受每窗32限制，total/omitted显式，不暗示全量展示。行号计算读取完整来源，单来源上限仍为索引16MiB，最近一个来源缓存限制驻留内存。

## Migration Plan

只新增读取字段和可选参数，无schema/FTS算法迁移。现有完整ref仍能回读；原两流search继承新增窗口，capture intent展示也沿用该有界结果。实际当前任务查询后按window.ref回读，刷新源码来源和Bridge当前视图时使用既有目录同步。
