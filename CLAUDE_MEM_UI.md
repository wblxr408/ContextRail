# ContextRail 本地界面

当前界面直接适配 claude-mem 的 My Work 与记忆浏览页，读取已有 ContextRail 任务库、原 Basic Memory main Markdown 和明确绑定的原生会话。其他客户端仍按需启用。

先完成项目客户端接线，再从项目根目录运行下列命令。启动器按需打开浏览器，同一项目和任务复用界面进程。关闭所有界面标签后空闲约 10 分钟退出，也可在工作台点击“关闭界面服务”。

| 页面 | 本机地址 | Windows 启动命令 |
| --- | --- | --- |
| ContextRail 工作台 | http://127.0.0.1:37778/rail | `./Open-ContextRailUI.ps1 -Page /rail` |
| Context Bridge 交接工作台 | http://127.0.0.1:37778/bridge | `./Open-ContextBridgeUI.ps1` |
| My Work | http://127.0.0.1:37778/ | `./Open-ContextRailUI.ps1` |

- **My Work**：原任务、来源、交付物和确切版本的人工查看标记。标记不表示验收、完成任务或移交权限；任务/交付物元数据变化后重新待查看。
- **记忆浏览**：原 main 笔记和绑定会话的已捕获记录；facts/narrative 为原来源预览。Markdown 回读最多128 KiB，卡片预览最多6144字符，原生大于256 KiB的记录保留身份并提示按需回读；历史材料不是当前指令。浏览有界，main 最多扫描5000条索引、每次列表最多覆盖300条最近笔记，原生每会话最多35条；不是全库搜索或全部会话历史。
- **本地工作台**：复用现有 `context.status` 的采集、处理队列、故障解释、注入回执；复用 `context.file_history` 与 `context.history_details` 回读文件历史；打开已登记交付物。来源读取失败保留上次资料并明确提示。
- **接线设置**：只修改当前 registry 的 Codex/Claude 项目开关；过期版本拒绝保存。原全局 Hook、账号、模型与记忆服务由现有配置管理。

仅监听127.0.0.1；请求需要精确Host，修改需要同源JSON。界面不能删除原记忆、写入账号/模型或任意指定磁盘文件。产物只打开原登记路径；元数据吻合也不等于内容验收。没有默认模型调用、外部遥测、云同步或额外注入。

**Context Bridge 交接档案**：双击桌面 `Context Bridge`，或运行 `./Open-ContextBridgeUI.ps1`，直接进入同一个本机服务的 `/bridge`。页面浏览当前项目 `.context-rail/bridge` 下的交接包，支持任务/版本筛选、有效与已替代要求、恢复材料和脱敏来源原文，以及复制精确交接包的恢复指令。列表最多500个交接包、目录最多3000个；单份账本最多1000项，单个原文最多展示前64 KiB，达到限制时页面说明省略。没有交接包时显示空档案，损坏的账本单独提示。

ContextRail 与 Context Bridge 保持两个独立界面及桌面入口，分别为 /rail 与 /bridge；底层共用按需启动和空闲退出机制。面板只读原交接文件，选择历史版本不会领取任务或把历史要求当成当前指令。桌面快捷方式可通过 `./Install-ContextBridgeShortcut.ps1` 重新创建。此查看页目前覆盖本项目的本地交接包；Basic Memory 检查点仍从“记忆档案”入口查看。

两套面板采用用户在 2026-11-04 确认的 Maple Ridge House 风格，包括暖灰底、20 px 圆角、同色浮雕卡片、内凹选中按钮和错落卡片布局。Bridge 的网页图标和桌面图标使用透明底，快捷方式安装脚本读取仓库内的 `contextrail/ui_static/bridge-icon.ico`。

2026-11-04 Bridge 接入阶段验证（样式修复未重跑这些用例）：`tests.test_bridge_panel` 5项当前行为用例通过（多版本/有效目标、原文范围及越界拒绝、空档案及损坏记录、变更/缺失版本、凭据脱敏）；新页面与导航脚本通过 JavaScript 语法检查。实际从桌面快捷方式打开 Edge，读取3个原交接版本，切换 revision-3、展开已替代要求、回读来源、复制恢复指令并筛选版本；截图位于 `.context-rail/desktop/context-bridge-panel.png`。未运行其他模块测试。

2026-11-04 样式按用户提供的 Maple Ridge House 截图修复；独立 ContextRail/Context Bridge 页头、暖灰双向阴影、圆角/内凹控件和卡片排布已通过实际浏览器查看。对应截图：`.context-rail/desktop/contextrail-maple-restored.png`、`.context-rail/desktop/context-bridge-maple-restored.png`。

原有边界仍适用：Codex 的远程 compact 被当前服务的 `model_not_found` 拒绝；原 main 模型召回曾超时/失败，能浏览Markdown不证明该召回服务已修复。

来源与本地改动：[UI 上游说明](ui/claude-mem/UPSTREAM.md)。本次浏览器验收、当前新增行为测试与资源测量见 OpenSpec change 中的 `ui-verification.md`。
