# Verification

## 本批范围与TDD

C05：命中上下文、标题、全局行号和精确UTF8字节锚点；同段相邻窗口合并，同标题不同span独立，代码依赖继续由宿主SelectionGroup表达。没有schema或FTS算法变更。

首个当前消费者用例把中文命中放在长段中部，期望snippet含“错误”；旧实现只返回块首160字符，取得实际失败断言。实现共享窗口投影后GREEN；当前两流与任务检索均复用投影，不再各自截取字符。

```powershell
C:/Users/wblxr/anaconda3/python.exe -m unittest tests.test_hit_windows
```

**11项通过，2.981秒，0 skip**，仅本批新契约，没有执行旧套件：

| 输入/状态 | 可观察结果 |
|---|---|
| 长段中部中文，前文emoji和CRLF | 预览包含词位，窗口ref回读等于text，词位ref回读等于term，全局行3 |
| 近处两个词与远处第三个词 | 近处合并、远处独立，每词精确引用 |
| 同名Markdown标题出现两次 | 两条hit/span保持，词位全局行2与4独立 |
| CR-only/CRLF与Unicode casefold | 原字节保持，KEYnEeDLE回读完整，行3 |
| 名称labeled.py但正文仅有labeled.py_extra | source_label，正文词位为0，未伪造子串命中 |
| 百次重复、六个远距词位 | 每窗32词位，省略68明确；window_limit2保留两窗、省略4窗 |
| 小JSON预算/非法窗口参数 | 完整JSON≤1800字节且有omitted，超范围/负数/bool参数拒绝 |
| 真失败Python子进程stderr | 执行failed保持，窗口锚点在真实原stderr第2行，逐字回读 |
| 实际CLI与官方SDK MCP | CLI可选参数有效，MCP查询窗口后context.get相等，非法参数isError |
| emoji密集窗口和非第一索引块 | 窗口≤2048字节、UTF8边界完整；后块词位仍是全局行3 |
| 500个K折叠为k的最长token | 1500原字节完整落入窗口，上下文缩短而非截断词位 |

Windows失败输出fixture显式禁用子进程TextIO换行翻译，避免已给CRLF被翻成CRCRLF后错误预期行号；保存的实际流原字节始终不归一化。

## 实际任务使用

操作前一致备份`.context-rail/backups/before-c05-windows-20261023-014914-199931.sqlite3`。实际任务self-use-01a161c7，owner host-01a161c7/epoch0；schema8保持。

先将当前contextrail/**/*.py显式同步至任务来源，run `450c83e70c904db784526503db38e8c3`，5件新/修改版本、42件未变、3个缓存目录跳过，complete无deferred/delete。没有向main记忆队列发送源码。

- query project_windows → contextrail/indexing.py revision3，结构跨度[12154,17261)，窗口[12259,12354)共95字节，line_start228/line_end230，实际词位[12299,12314)在全局行229；窗口及词位分别按ref精确读回。window_limit1另有1窗省略，回执如实保留。
- query 原文 → Bridge原始来源revision1，第904行窗口[49706,49799)共93字节，实际词位[49733,49739)也分别回读。该来源是创建时已保存证据，不能当作最新TODO内容。

完整回执`.context-rail/c05-window-receipt.json`。事实版本63→68，固定Bridge base a558284943e241eab297739aca0864f2保持；刷新后的当前视图006cb969bd7140299abfeebd526af341已写active-native-client.json，目标仍是根据todo做出持续实现。

## 项目检查与状态

wheel离线构建成功，退出0；既有bdist_conda缺conda.auxlib.packaging警告不阻止wheel。OpenSpec严格验证、所改已跟踪文件git diff --check通过。

旧回归套件没有运行。A06原生Codex任务Hook仍缺实际调用证据，用户trust确认已保留。Basic Memory恢复后，上一离线交接已能通过原生read_note读到并返回permalink；其不可变正文中的旧unconfirmed描述保持为当时状态。
