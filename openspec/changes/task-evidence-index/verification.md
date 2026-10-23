# C03 验收

2026-10-22，Windows/Python3.11，SQLite本机构建支持FTS5。

## 当前契约与RED

先保存包含中文、emoji、CRLF和末尾socketretry的真实Store工件，调用search-indexed CLI期望成功及精确候选跨度。首次RED为缺子命令、退出2。实现schema7、同步索引、受租约重建与MCP后，首批7项行为通过；旧快照用例曾错用load_snapshot参数/字段，按现有require_current和顶层objective修正夹具，不计产品RED。

随后新增结构覆盖用例：Python模块前言、顶层赋值、Markdown标题前正文实际查不到，三项断言RED。复用uncovered_spans补raw-gap后通过；算法版本改为lexical-spans-v2，旧派生版本不能伪称完整覆盖。

最终 `.venv/Scripts/python.exe -m unittest tests.test_task_evidence_index`：**10项通过，2.603s，无跳过**。

| 当前需求 | 实际观察 |
|---|---|
| 真实CLI/精确跨度 | 大日志末尾中文/emoji/CRLF片段命中，跨度非0且≤8192bytes，ContextTools回读与原始对应字节一致。 |
| 候选排序/任务隔离 | retry/socket相关短证据先于仅retry噪声；同名其他task的独有内容不混入。scope覆盖拒绝。 |
| 版本/生命周期 | 新revision替代旧候选，旧ref原文可读；invalidate、expiry、purge均不返回不可读候选。 |
| 原子提交 | 第二件index_state插入由SQLite trigger真实拒绝，工件、任务版本与第一件FTS条目全部回滚。 |
| 真实schema6升级 | 用旧schema实际创建任务、约束、工件与快照，升级后pending可见；错epoch拒绝，owner重建后可查；原任务/快照/原文保留，事实版本不改。 |
| 资源与排除 | 非UTF8原文仍可读、excluded=1；超过16MiB全文不索引、pending=1，不误报no_match。 |
| raw-gap补齐 | 模块前言、顶层赋值、函数及Markdown前言/章节各词均可查，并在返回跨度实际读到该词。 |
| 重建/预算 | 重建两次结果来源一致、事实版本不改、其他task索引仍可查；1600-byte完整JSON省略可见，极小预算/长查询拒绝，FTS语法字符作数据。 |
| 不可用能力 | 显式关闭backend能力夹具下保存/字面回读仍工作、pending可见、索引查询unavailable；这是能力分支夹具，不是另编译了无FTS5的SQLite。 |
| 真实MCP | 官方SDK实际stdio搜索，再按返回ref读到中文CRLF原文；index_status的task覆盖拒绝。 |

## 本项目实际库

在已经存在的ongoing task self-use-01a161c7上实际执行：先SQLite一致备份，再从schema6升级至7，owner/epoch明确重建。回执 `.context-rail/c03-rebuild-receipt.json`：

- 升级后待回填10件，重建后indexed=10、spans=1124、pending=0。
- 完整tasks行在重建前后相同，包括目标、约束、owner/epoch/version。
- 原快照a558284943e241eab297739aca0864f2仍经Bridge源复核编译，objective=根据todo做出持续实现。
- query“自用”实际命中12片段，第一条引用实际回读含原查询。
- 一致备份 `.context-rail/backups/before-c03-rebuild-20261022-140756-525613.sqlite3` 保留。

## 构建与边界

最新源码wheel离线非隔离构建成功，沿用Anaconda解释器；bdist_conda缺conda.auxlib.packaging警告非致命。`openspec.cmd validate task-evidence-index --strict`、`git diff --check`通过。

未运行旧批次/全项目回归，不声称整个项目已回归。没有smoke、哈希比较、文件存在或浅启动探针。变更仍未提交/推送。FTS5缺失的真实编译构建未验收；当前正常SQLite和明确能力夹具分别记录，不能混称。BM25为共享统计的词法候选，不声称语义检索/跨任务分数可比/所有跨片段词均可召回。
