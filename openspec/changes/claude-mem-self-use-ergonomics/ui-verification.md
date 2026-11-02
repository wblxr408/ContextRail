# 本地界面接入验收

2026-11-01，Windows 本机执行和部署。用户明确启用原后置 CM08/CM11/CM21；未扩展 Codex/Claude 之外客户端。工作实现位于隔离任务分支 codex/cm-stage1-20261101，宿主相关验收与日常接入位于 D:/ContextRail，保留该处其他在途修改。

## 实际实现

固定上游 b80b29bc49d08a71312f44b8feacb2e7585edff4 的 My Work、Viewer、SessionDetailPage、映射、分页、连接新鲜度和查看客户端保留主体源码。最小补丁提供现有 registry 设置、只读原记忆、查看语义和来源故障。源码/改动/构建归属在 ui/claude-mem/UPSTREAM.md；LICENSE、NOTICE、字体许可与构建依赖声明随静态资源提供。

新增 Python 标准库本地 HTTP 适配器 contextrail/ui_server.py，使用既有任务库、主记忆 Markdown 和绑定的 normalized_events/source_events。故障、文件历史与产物查询调用原 ContextTools。人工查看只写原 R 的 UI 元数据，配置只调用原 toggle_client；未登记新 observer、记忆库、MCP catalog、登录任务或模型归纳进程。

浏览器入口 http://127.0.0.1:37778/ ，工作台 /rail，记忆 /viewer。项目根目录 Open-ContextRailUI.ps1 提供后台启动入口；同项目/任务复用已有实例。SSE 连接保持页面存活，页面断开后无连接且空闲约600秒退出；也可用工作台显式关闭。自动退出时长已配置，未用定时等待替代业务验收。

## 当前新增行为测试

只运行 `python -m unittest tests.test_local_ui.LocalUI -v`，6项全部通过。这是原存储和实际 HTTP 的消费行为断言：

1. 原任务目标、原 main 笔记和真实已登记产物投影；读取不改变任务权威。
2. 已查看保存、再次读取保留、新任务版本重新待查看、旧版本409。
3. 项目开关保存到原 registry、保留另一个客户端/记忆配置、过期配置409。
4. 外部Origin/Host、任意磁盘路径和模型配置扩展被拒绝。
5. 原生正文、原会话计数与项目筛选；超过256 KiB的正文保留身份并提示回读，普通记录仍返回。
6. 产物列表顺序变化后仍打开原登记的确切 Ref；不把页内索引当持久身份。

已观察到的RED包括最初HTTP404、查看接口返回封装而非上游直接快照、SQLite行访问导致原生读取400、会话虚假固定计数、确切产物Ref400和大正文仍直接预览。修正后以上消费断言GREEN。连接时间戳也在真实页面先显示持续连接中，再修正到上游支持的毫秒精度后出现 Live updates。

最初一次未限定 TestCase 的调用意外发现了导入的旧 CompactAssistance 测试，观察到前两项通过后停止；随后改为模块别名并仅运行 LocalUI。没有把误运行的旧测试当本次完成证据，也没有继续旧套件。未运行 smoke、hash/措辞/文件存在性/import/启动/浅健康探针。

## 构建与实际浏览器

My Work 的 TypeScript/Vite生产构建通过，Viewer类型检查与esbuild生产构建通过；使用独立锁文件。主包 wheel 重新构建通过，OpenSpec strict校验通过。Vite仍报告约1 MB入口的分块建议和未来configLoader提示；不影响本机生产静态资源。构建环境另有Anaconda bdist_conda入口警告，wheel最终成功。

真实 IAB 浏览器已经完成：

- My Work 展示原任务和3个原交付物，任务详情打开，来源传输状态为 Live updates。
- 标记当前版本已查看，页面收到保存回执，刷新后保持；Undo成功且回到待查看。任务仍为原状态。
- Codex 项目开关暂停、保存、再启用；Claude始终启用；最后两者均启用。
- 记忆浏览读取原 main 笔记和绑定原生记录。会话目录显示原 main 786篇及原生1595/46/28条记录（现场时间点统计），开始时间未知明确显示未记录；打开绑定Claude会话的SessionDetailPage后展示已捕获正文预览。
- README.md返回10条相关历史，并回读到确切Claude事件 source-event-v1:22ce52b41eaf52db93ab32f7e4d4cf36，包含原路径/事件/会话身份；未领取任务。
- 本轮接入结果另以原宿主租约登记本地界面指南与工作台链接到 deliverables/claude-mem-local-ui-20261101/record revision 1，保留旧产物。
- 从真实页面打开确切登记 Ref 的A–D指南，浏览器显示真实Markdown正文。改动后的当前文件显示changed，另一份审计显示metadata_matches；远端链接仍标not_checked_remote。
- 工作台显示原队列149项待处理、原故障/退避记录与压缩返回回执；过期重试记录明确说明是否恢复未知。显式关闭界面按钮已实际操作成功。

## 内存

实际浏览记忆时，未限制原生大正文的服务主进程峰值100.16 MiB；UI预览增加256 KiB原正文门限并保留精确身份后，重新浏览相同绑定来源，服务主进程峰值36.00 MiB、工作集32.20 MiB，Windows Python启动器约3.60 MiB。两进程当前合计约35.8 MiB，峰值各自相加的上界约40.0 MiB（不是同时峰值测量）。随后复用原文件历史查询时，主进程又观测到99.71 MiB短时峰值，回落后工作集8.67 MiB、私有提交23.91 MiB。因此36 MiB只代表优化后的记忆预览浏览，不能作为所有功能的峰值上限。这是本轮交互观察，不是长期负载基准。

以上不含浏览器标签页、已有记忆/向量服务，或原本按需运行的短时Node格式化子进程。没有为界面运行Vite开发服务器或整套上游worker/Chroma。主进程大正文限制不改变原记录及现有精确回读能力。

## 用户反馈后的启动修复

用户使用 Windows PowerShell 5.1 执行 `./Open-ContextRailUI.ps1` 时，原无 BOM UTF-8 中文报错字符串被按 ANSI 误读，导致后续路径/引号出现 ParserError。现将启动脚本保存为 UTF-8 BOM，并显式按 UTF-8 读取原 active-native-client.json。

本次仅运行新目标 `tests.test_ui_launcher_encoding.WindowsUILauncherEncoding.test_windows_powershell_dispatches_exact_chinese_registry_and_options`。真实 Windows PowerShell 读取实际脚本字节和含中文目录/registry 的 UTF-8 配置；测试只拦截 OS 进程创建，断言恰好一次启动意图、正确路径/参数、工作目录与隐藏窗口。原实现观察到0次派发及用户同类解析失败，修复后该消费断言通过；期间纠正测试拦截器的脚本作用域问题，不将它算作产品RED。未重跑旧6项或其他回归。

实际执行 `WindowsPowerShell/v1.0/powershell.exe -NoProfile -File D:/ContextRail/Open-ContextRailUI.ps1` 返回0，并在浏览器读取到 self-use-01a161c7 的原任务、原交付物和双端启用状态。当前修复在 Windows 本机完成，属于已授权的宿主相关交付修复。

## 用户指定 MedPanel 样式与新 Logo

已打开用户指定的 Kombai MedPanel 完整说明和实际预览，查看原金奶油画布、文件夹面板、珊瑚/蓝绿、细描边及26px标题。参考页的通用站点 branding 与实际预览不同，本轮依据实际预览。应用到三页；新透明PNG原样接入页头/favicon。详细设计、字体和来源在 CONTEXT_RAIL_BRAND.md。

原工作台四区与旧claude-mem标识作为视觉基线。当前六面板显示原任务、采集1669、待处理149、产物5等现场原记录，既不填医疗样例，也不增加质量估算。My Work和记忆页已在真实浏览器查看共同主题与新品牌导航；窄屏记忆来源下拉原有隐藏规则被共同主题覆盖，筛选控件重新可见。

正常宽度与390px窄屏实际查看：工作台六面板窄屏均为333px单列，文档宽/滚动宽均375px；My Work窄屏宽/滚动宽均375px，记忆页390/390px。视口已恢复默认。此次只做变化范围的视觉基线/排版审阅、现有My Work TypeScript/Vite生产构建及Viewer类型/esbuild构建，不运行旧业务回归、启动/健康或文件存在性探针。品牌函数替换时构建发现原生Agent路径常量被过宽替换，已从原版本恢复其原定义，最终类型与构建通过。

## 保留的缺口

OpenSpec5.4仍仅余Codex真实compact模型服务拒绝（model_not_found）；未更换账号或模型。原 main 模型召回曾超时/失败，Markdown能浏览不等于召回服务恢复；工作台如实显示队列与失败。UI预览为有界最近资料，既不提供全库全文搜索，也不把未绑定会话自动加入。人工查看依据任务版本和文件元数据，不能当作正文验收。Basic Memory写工具仍不可用，交接/经验待同步草稿保留在当前聊天outputs，未声称已索引。

## 固定导航、一屏工作台与多栏档案（用户布局纠正）

用户反馈截图所示导航在三页间跳动，桌面工作台有不必要整页滚动，记忆档案需要两至三栏。本轮保留 MedPanel 的视觉规则与原数据来源，新增单一 rail-shell.js 页头组件，三个静态入口均在 React 根节点之外直接使用，原主题/来源/连接控件通过 portal 与 slot 保留；加载状态不卸载导航。页签宽度固定88px，只有当前态改变。

当前本机1280×720：改前工作台 document 高1095px，导航x=241.60；My Work 导航x=167.14、y=31.81；档案导航x=211.70。改后统一导航x=361.20、y=0、总宽274px，三页保持一致。工作台 document 为1280×720，页尾底边708px，六面板同屏；长交付物、故障及展开详情仍在所属面板内滚动，不丢弃记录。小于1051px宽或680px高时恢复自然页面滚动。没有将文字或页面整体缩放来凑屏。

档案1280px为三列390px，1024px为双列466px，390px为单列352px；390px的页面client/scrollWidth均为390px。卡片来源与工具拆成两行，避免多栏挤压导致英文标签逐字折行。档案实际滚动720px后共用页头仍top=0。viewport已恢复默认。截图：当前聊天outputs/context-rail-one-screen.jpg与context-rail-memory-columns.jpg。

My Work TypeScript/Vite、Viewer TypeScript/esbuild与OpenSpec strict验证通过。共用页头移除旧Navbar后，构建报告未使用的旧页头类型/标题变量，已删除这些旧呈现变量后重建通过。没有执行旧业务回归、启动/健康/导入探针或样式字符串断言。实际部署为Windows本机既有服务的静态资产热读取，没有重启原Hook或记忆服务。任务5.4既有远端compact限制保持原记录。