# Verification

本批实现C01/C02。功能范围是明确目录观察、持久续读、文件版本/删除协调、只读状态回读和显式复用原文档队列；D06知识页联动及E02自动工作现场事件仍未完成。

## TDD与当前行为证据

最初真实CLI用例期望directory-sync保存所选UTF8源码，实际因缺命令而失败。随后最小闭环GREEN，继续本批生命周期边界：分页删除用例先失败（一次删完，期望partial），加入有界协调；并发来源head变化先未拒绝，加入stage版本检查；文件名查询实际优先返回空白行，新用例先失败后排除目录纯空白跨度。

交付复核新增页总读取容量用例：旧实现先完整读下一文件才判断页上限，10字节fixture页实际读12字节，用例先失败；改为读取前stat容量判断并将打开读取限制为剩余额度，再GREEN。

最终执行：

```powershell
C:/Users/wblxr/anaconda3/python.exe -m unittest tests.test_directory_sync
```

**18项通过，38.635秒，0 skip。** 仅当前变更行为用例，未运行旧套件。覆盖：

| 行为 | 证据 |
|---|---|
| 非Git工作区的嵌套gitignore/否定、glob和默认依赖排除 | 原生Git实际调用，最终仅选定两文件，UTF8/emoji/CRLF逐字节回读 |
| 已跟踪文件也执行选择忽略 | git init/add真实fixture，--no-index排除已跟踪文件，显式禁用后保存 |
| 进程退出后的精确续读和状态分页 | 三次CLI进程按run/step继续，过期step拒绝，只读连接按next_offset逐条回读 |
| 大小/深度/目录枚举上限与binary | partial具体原因、增预算续读、binary原字节保留且索引excluded |
| 内容修订和删除重现 | 保留mtime的改动产生r2，仅touch不产生新revision，旧r1历史可读，删除r2不可用，再现r3 |
| 不完整扫描保守删除 | 未见旧文件在partial时仍可查询，完整扫描后才失效 |
| 有界删除与来源隔离 | 每页删除1件，其他source检索保持 |
| 错scope/owner、freeze、superseded和policy变化 | 原生Store租约与handoff、ContextTools拒绝错误绑定/游标 |
| 原子性和竞争 | 第二次真实索引插入trigger失败后无工件/条目/游标推进，同run step0可重试；读期间实际Store head变动拒绝stage提交 |
| Windows外部junction | 实际mklink /J，跳过链接且拒绝显式越界root，清理只移除junction本身 |
| schema7→8 | 实际旧schema fixture升级后原任务/原文/快照保持，新目录导入与检索可用 |
| 真MCP消费者 | 官方SDK stdio目录分页后context.get回读中文CRLF，注入root参数返回invalid_request |
| 显式M队列 | 加载现有listener.Store/history_import.enqueue，临时已存在state.sqlite；冻结文档在磁盘已修改后仍送原文，第二次入队0新增、实际jobs仅1行，.py=unsupported |
| 文件名查询可读原文 | 结构空档空白不抢占目录path候选，所有返回span含非空白来源内容 |
| 页聚合读取容量 | 两件各6字节，页预算10字节时第一批实际只读6字节、第二批继续，不先读后丢弃超额文件 |

额外按文档使用的本项目`.venv/Scripts/python.exe`执行同一当前M入队用例，**1项通过，3.362秒**，证明实际工程环境可加载既有应用依赖。临时队列没有worker，不是全局main分类/知识页刷新证明。

## 实际项目使用

任务self-use-01a161c7，owner host-01a161c7/epoch0。只导入contextrail下**/*.py，未向全局main队列发送源码。

- 一致备份：`.context-rail/backups/before-c01-sync-20261022-153237-426485.sqlite3`，该时实际库已为schema8，事实版本14。不能把这次操作说成在真实库做了7→8迁移；7→8证据是上述真实旧schema fixture。
- 初次run `86b980fd018b49f3aaf428e901b4fc4c`：6页，每页最多8文件，保存46件，跳过3个依赖/缓存目录，pending=0。回执`.context-rail/c01-first-source-import-receipt.json`保留首次查询命中1空白字符的问题。
- 空白候选修复后中间run `83add969bd864c3483ef242918a85eb2`更新2件、44件未变；页容量修复后的最终run `198827c4fe7f4d0abcad574a069f4457`仍分6页，**更新1件、45件unchanged、跳过3项**，无deferred/delete，状态complete。最新回执`.context-rail/c01-sync-receipt.json`；最新操作前一致备份`before-c01-sync-20261022-153855-743835.sqlite3`。
- 实际文件名query directory_sync.py返回来源contextrail/directory_sync.py的revision3、bytes[0,91)，回读91个来源字符；此前空白行问题已经功能修复和实际使用复核。
- 当前索引59件、1968跨度、pending=0、directory_pending=0；事实版本63。固定Bridge base a558284943e241eab297739aca0864f2保持，刷新后的当前视图535fb02dbe434ac6a36f2a50a05525bf已写active-native-client.json。

实际扫描是观察当时磁盘的显式操作，后续编辑需要新sync。创建时bundle内TODO仍是旧快照；当前进度以仓库TODO/OpenSpec为准。

## 项目检查与边界

`uv build --wheel --offline --no-build-isolation --out-dir dist --python C:/Users/wblxr/anaconda3/python.exe`成功，退出0。既有bdist_conda入口缺conda.auxlib.packaging警告非本批构建失败；wheel构建成功。OpenSpec严格验证通过，所改已跟踪文件git diff --check通过。

旧回归套件未执行，遵守当前dev范围。A06仍缺Codex原生任务Hook实际调用证据，用户已确认trust，不重复询问。Basic Memory MCP队列繁忙，原生搜索/读取/写入及同配置前台连接均未成功；不将保存草稿或队列超时声称为知识库保存成功。
