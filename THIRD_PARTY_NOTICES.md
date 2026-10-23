# 上游适配来源

本地研究副本位于 `.context-rail/upstream/`，不作为 Python 运行依赖，也不运行其安装或后台脚本。

E05/E06/I02实际阅读ai-memory固定`1bd866375b01031d63db54862a133376598d3701`的`ai-memory-store/src/ops.rs::accept_handoff_in_transaction`：借鉴领取时重查源状态/范围、显式CAS与候选/领取分离，接到原Rail handoff与events。没有复制Rust实现、默认SessionStart领取、同cwd最新候选或过期清理；Bridge要求/原文span与workspace writer继续各自原契约。context-mode快照只作经历/当前要求分层参照，不继承固定截断。

D07/H03/D08适配context-mode同固定commit的`src/adapters/codex/usage.ts::parseCodexUsage`为`_vendor/context_mode_usage.py`（ELv2，MODIFIED）：保留last/cumulative及未知、不把reasoning额外加入output、不提供pricing或第二文件游标。使用原事件owner的真实来源ID/文件generation+offset；相同计数不是来源身份。`persist-tool-calls.ts`跨重连持久计数的接线思路用于原R events，不复制其latest-session自动选择。所有差异在ACCOUNTING_USE.md说明。

D01/D05/H02将ai-memory同固定commit的`ai-memory-core/src/excerpt.rs`、`profile.rs`元数据标签及`ai-memory-hooks/src/brief_tiers.rs`覆盖→L1→短正文→低成本L2晋升顺序移植为`contextrail/_vendor/ai_memory_brief.py`（MIT，MODIFIED）。本地将字符串成本算术改为完整JSON重新计量，目录保留Rail精确ref，重叠分组由Rail先合并；不继承pinned slots/recent区份额。JSON可使用abstract/summary；Markdown frontmatter跳过，未复制YAML解析。摘录不是全文覆盖承诺。

| 项目 | 固定 commit | 当前用途 | 许可 |
|---|---|---|---|
| akitaonrails/ai-memory | `1bd866375b01031d63db54862a133376598d3701` | `sanitize.rs` 的 28 条凭据规则、shadow span 思路移植为 Python；来源代码版权属于 Fabio Akita | MIT，完整许可见 `contextrail/_vendor/ai_memory_LICENSE.txt` |
| mksglu/context-mode | `90af14e34bf35120c3310f7a75f54fa99d2cabba` | E01移植 `src/session/extract.ts` 的31项工具名称映射和normalizeHookInput语义；保留原始工具名 | ELv2，完整许可见 `contextrail/_vendor/context_mode_LICENSE.txt` |

ai-memory 原始地址：https://github.com/akitaonrails/ai-memory/blob/1bd866375b01031d63db54862a133376598d3701/crates/ai-memory-core/src/sanitize.rs

F01还适配同commit的`crates/ai-memory-store/src/ops.rs::insert_observation_keyed`：来源键与事件在同一事务中认领/提交，保留项目归属先验证的顺序。实现为`contextrail/source_events.py`，使用原M jobs及兼容别名，不复制上游TTL和整套store。

E01的context-mode移植位于`contextrail/_vendor/context_mode_tools.py`，已显著标注MODIFIED、原版权及ELv2。工具名称映射从TOOL_NAME_NORMALIZE直接提取，normalizeHookInput逻辑改为Python函数并额外保留original_tool；没有复制它的语义错误推断、近期内容去重、后台或整套Node运行时。该移植文件保留原ELv2，不将其标为MIT。

E08另将同commit `src/session/error-classifier.ts::classifyError`的10类有序规则移植为`contextrail/_vendor/context_mode_errors.py`（ELv2，MODIFIED）。只在来源已声明失败/超时的派生时间视图中使用，保留原状态与原文引用；分类明确是启发式。未复制命令/耗时分类或引擎。ai-memory同固定commit `reader.rs::session_observations_scoped`的范围过滤、实际时间/ID排序及分页方式作为查询适配参照，未复制Rust连接池/权限层。

C06/C08/H01阶段将同commit `src/search/unified.ts`的多来源合并结构适配为`contextrail/_vendor/context_mode_unified.py`（ELv2，MODIFIED）。修改为两种视图均合并三源、源失败显式报告、未知时间不以session启动时间补造、按来源内排名合并。没有复制auto-memory文件夹扫描器；main继续由原记忆资源服务拥有。

F01长连续文本导入暴露Python re与Rust regex的引擎差异：本地URL规则加scheme起始边界，auth header名称前缀限制为128字符，避免无界回溯，策略升级v2。已由实际导入用例观察超时后修复；不是Rust实现的完全等价移植。

本地调整：Rust regex 改为 Python stdlib re；env assignment 规则改用有界值捕获，避免吞掉 JSON 分隔符及重复脱敏占位符；增加 JSON/嵌套敏感字段、参数数组、同一 capture 内已知秘密的副本处理、输入资源限制、固定错误、角色与策略类型。未移植上游可配置 allowlist、全部 Unicode 隐藏文本清除与 Rust 持久化类型；不宣称完全等价。
