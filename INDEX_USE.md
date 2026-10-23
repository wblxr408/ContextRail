# 任务证据全文索引

C03 为已经保存的当前工件建立可重建的 FTS5/BM25 候选索引，新增 `context.search_indexed`、`context.index_status` 和对应 CLI。既有 `context.search` / `search` 仍是字面子串通道。

## 查询与原文回读

```json
{"query":"socket retry 错误","limit":10,"offset":0,"budget":8192}
```

以上参数交给 `context.search_indexed`。每条 hit 的 ref 包含精确 name/revision/start/end，可直接交给 `context.get`；范围为左闭右开 UTF-8 bytes，保留中文、emoji、原空白和 CRLF。片段最多8192bytes，snippet 是可截短预览，不能替代原文。

搜索结果还给出匹配词、BM25相对分数、排序算法、总候选数、展示数、省略数和 next_offset。FTS使用原始词的安全编码，不接受SQL/FTS表达式；query中的OR、引号或括号仅作普通输入分词。查询最多1024字符，使用前16个去重词，未用词显式返回。

词法规则沿用已有完整英文/标识符词与中文bigram。不是语义检索、拼写纠正或任意子串检索；名称和正文分别加权。BM25使用共享索引统计，但返回条件固定为本次scope、当前head和有效来源，只比较同次查询排序，不比较不同任务分数。[SQLite FTS5文档](https://www.sqlite.org/fts5.html)

## 覆盖与状态

`context.index_status` 不接受task/scope覆盖，不会自动回填。它返回：

| 字段 | 含义 |
|---|---|
| indexed / spans | 已完成当前版本索引的工件和片段数 |
| pending | 尚未回填、索引版本已变或超过资源界限的当前工件 |
| excluded | 非UTF-8证据，原始二进制仍保存可读 |
| unavailable | 失效/过期的当前head，不参与候选 |
| complete / coverage | 对当前可读UTF-8、声明限制范围内的覆盖状态 |
| reasons | 各类未索引/排除原因的计数 |

无候选且pending>0时是 `index_pending`，不能断言所有材料都没有匹配。已有候选也需查看index.complete。FTS5不可用时索引查询返回unavailable，核心保存/字面搜索/精确回读继续工作；不会下载SQLite扩展或启动新服务。

输入超过16MiB或结构片段超过4096时保留pending，整份原文保持，不能只索引前缀后报告全量完成。每个完整词最多512 UTF-8 bytes，过长词不入词表；中文bigram仍保留。跨8192byte片段边界的词可能需要字面搜索/原文回读。完整返回JSON受budget及reader读取额度限制，按整条hit省略；连必要元数据也装不下则budget_exceeded。

## 新写入与旧库

新证据、当前revision与派生索引状态在同一SQLite事务提交。第二件索引写失败会让整个put_many回滚，避免正文和检索视图各提交一半。新revision替换旧候选，旧revision仍可显式回读；失效/过期不返回可读当前候选，purge同步清理失效派生文本。

结构块未覆盖的Python模块前言、顶层赋值、装饰器或Markdown标题前正文以raw-gap跨度补齐；不把结构化分块误当成全文覆盖。当前索引算法版本 `lexical-spans-v2`，算法版本不符时报告pending，重建后才使用。

Store schema升级至7。旧库打开时只建立派生元数据并标pending，不加载全库正文回填，也不改任务、租约或快照。索引不是长期知识权威。

C01/C02现已将Store升级至schema8，目录来源和精确续读见[目录指南](DIRECTORY_USE.md)。当前索引覆盖还包含directory_pending；目录部分扫描时不能根据已保存证据的空结果推断未扫描文件无匹配。

C05的检索命中新增windows、snippet_ref及全局行号，详见[窗口指南](HIT_WINDOWS_USE.md)。hit.ref仍指原结构跨度；窗口可各自按真实字节ref回读。

## CLI与按任务重建

```powershell
.venv/Scripts/python.exe -m contextrail index-status --workspace D:/my-project --task my-task --session owner
.venv/Scripts/python.exe -m contextrail index-rebuild --workspace D:/my-project --task my-task --session owner --epoch 0
.venv/Scripts/python.exe -m contextrail search-indexed --workspace D:/my-project --task my-task --session owner --query 'socket retry' --limit 10 --response-bytes 8192
```

scope由相同项目配置与显式参数决定。重建要求当前owner/epoch且交接未冻结，只影响指定任务、可重试，不推进事实版本；模型只读工具不能重建或取得写权。旧程序不认识schema7，回退不能简单降低user_version或只恢复索引；保留权威库备份并使用兼容代码。

本项目实际任务 `self-use-01a161c7` 已从schema6升级并重建；该次验收为10件当前证据、1124片段、pending=0。用户目标/租约/原快照保持，实际query“自用”命中12片段并按ref读回。之后的新观察会正常增加工件并推进task version，当前覆盖用index-status读取。操作前一致备份与回执分别在 `.context-rail/backups/before-c03-rebuild-20261022-140756-525613.sqlite3`、`.context-rail/c03-rebuild-receipt.json`。源bundle内TODO是创建时快照，当前工作进度以仓库TODO/OpenSpec为准。

当前消费者及真实MCP验收见 openspec/changes/task-evidence-index/verification.md。
