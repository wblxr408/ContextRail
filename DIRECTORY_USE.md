# 目录导入与增量同步

C01/C02把明确工作区内的源码或文档保存为任务证据。用同一root再次同步会比较实际字节，修改产生新revision；相同内容和仅mtime变化不重复保存。每个来源绑定Scope和实体workspace/root。路径、正文与目录条目都是不可信数据。

## 配置与第一次导入

先按SELF_USE.md初始化项目任务。将以下段落加入项目`.contextrail.toml`，include/exclude相对选定root，capture.ignore仍相对workspace。

```toml
[directory]
include = ["**/*.py", "**/*.md", "**/*.txt", "**/*.json"]
exclude = ["private/**"]
respect_gitignore = true
max_files = 32
max_depth = 6
max_file_bytes = 1048576
max_directory_entries = 20000
```

```powershell
.venv/Scripts/python.exe -m contextrail directory-sync --workspace D:/my-project --task my-task --session owner --epoch 0 --root src --response-bytes 8192
```

include为空表示所有普通文件；模式无`/`时匹配任一路径段，有`/`时按路径段匹配，`**`支持零层或多层。它们是glob，gitignore另用原生`git check-ignore --no-index -z --stdin`，包含嵌套规则、否定规则和已跟踪文件的忽略判定。普通非Git目录使用临时Git元数据，不初始化用户目录；Git不可用时明确报错，可在配置中显式禁用respect_gitignore。

默认跳过node_modules、.venv/venv、__pycache__、build/dist/target、.next/.nuxt及常见工具缓存目录；.git、.context-rail和配置的Store文件始终跳过。symlink、Windows reparse/junction与特殊文件不读取。选定root必须在workspace内，打开文件后校验句柄路径/身份，读取前后复核stat；Windows枚举期间固定目录组件，无法验证时保留为deferred。UTF-8/CRLF原始字节保留，非UTF8以二进制证据保存、全文索引明确excluded。

## 部分覆盖和精确续读

响应含run、step、status、pending、counts与resume。达到数量/深度/大小/枚举上限，或I/O错误，会保留partial及原因；不会把没读到的文件当作没有匹配。每页暂存至多16MiB原文，读取前先检查剩余容量，每次打开读取允许额外1字节检测文件增长；目录枚举默认每层20000项，工作队列元数据最多8MiB。硬上限为每页1000文件、深度64、单文件16MiB、每层200000项。超硬上限应缩小root或显式排除。

```powershell
.venv/Scripts/python.exe -m contextrail directory-sync --workspace D:/my-project --task my-task --session owner --epoch 0 --resume EXACT_RUN --step EXACT_STEP --max-files 64 --max-depth 12 --max-file-bytes 2097152 --response-bytes 8192
.venv/Scripts/python.exe -m contextrail directory-status --workspace D:/my-project --task my-task --session owner --run EXACT_RUN --offset 0 --limit 20 --response-bytes 8192
```

预算可增加；run的root、选择策略、capture.ignore和Store绑定固定，变更时新开run。新run替代同source旧run，旧游标或已推进step不能重复提交。进程退出后可按run/step继续；事务失败后同一步重试。不要把更新响应观察超时视为失败并新开run：先查询directory-status，使用实际step。

counts是累计观察事件，包括先deferred后captured；pending才是当前队列项数。条目按事件顺序分页，省略完整条目时保留next_offset，必要元数据装不下返回budget_exceeded。写入至少需要1536响应字节。目录状态只读MCP为`context.directory_status({run,offset,limit,budget})`，模型不能选文件系统root、写证据或入记忆队列。条目中的ref可直接交给context.get。

## 修改、删除和历史

再次执行directory-sync --root src开始新扫描。只有所有待扫描项成功消化，才将本来源未再见到的旧文件失效；达到深度、大小上限或出错时绝不猜测删除。删除协调也按文件预算分批，可能在扫描完成后仍返回partial。改选择规则后明确排除的旧文件在该完整扫描后退出当前来源。

每页工件、索引、来源映射、观察条目和游标在同一SQLite事务提交，复核owner/epoch、handoff冻结、run step和文件工件版本。旧内容仍可读时availability=historical，当前head为current；删除/失效/过期为unavailable。其他task/source不受影响。扫描是逐页观察，不是文件系统原子快照；两页间变化由下一次同步发现。

search-indexed命中补source_path，路径本身可查询；index-status的directory_pending使部分目录的空结果保持index_pending。明确result_ref的两流查询仍只报告该结果自己的索引覆盖。完整目录观察也仅覆盖所选范围与当时观察的版本，不代表磁盘未来不会再变。

目录来源的纯空白结构空档不作为文件名候选；非空原文跨度与完整工件仍保留，避免路径查询首条只读到一个换行符。

## 显式送入既有工程记忆队列

```powershell
.venv/Scripts/python.exe -m contextrail directory-memory-enqueue --workspace D:/my-project --task my-task --session owner --epoch 0 --run EXACT_RUN --memory-home C:/Users/me/.codex/services/engineering-memory --offset 0 --limit 20 --response-bytes 8192
```

只处理该run已保存的.md/.txt/.json，读取冻结revision而非重新读磁盘，沿用原服务UTF8-BOM/通用换行、clean脱敏、段落分片和history_import.enqueue。源码.py只作为任务证据，返回unsupported。默认从既有memory-home/app加载代码；分离数据目录可显式给可信--memory-code，已有state.sqlite必须存在。该命令不启动分类worker或新增默认监听器。

身份绑定scope/source/ref revision和首次版本时间；同一版本重送不新增jobs，跨不同导入器的全局去重仍属F01。状态queued_not_yet_classified只证明排队，未声称知识页已刷新。按next_offset继续，partial_failure/丢失回执时重送同页；原队列稳定ID允许一次业务效果。M队列和Rail不是跨库事务，超时后应重送同版本，不猜测零效果。源变更到知识页stale/再生成仍由D06负责。

## 数据库与证据

Store schema8新增来源/run/文件映射/观察条目四张表。schema7升级保留既有任务、快照与证据；旧程序拒绝新schema，回退使用一致备份和兼容代码，不降低user_version。当前变更行为证据及实际项目操作回执见openspec/changes/bounded-directory-sync/verification.md。
