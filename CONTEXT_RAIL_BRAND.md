# ContextRail 本地界面视觉

当前视觉以用户在 2026-11-04 提供的 Maple Ridge House 截图为准。已直接读取 [Maple Ridge House 参照](https://agent.kombai.com/kombai-inspiration-library/app-pages/dashboards/web/17/index.html) 的实际颜色、圆角和阴影：暖灰底、同色浮雕卡片、按压式按钮。此前将“原风格”解释为 MedPanel 是错误判断；后续不得以旧 MedPanel 文档覆盖这次明确提供的截图。

## 设计落点

| 项目 | 当前应用 |
|---|---|
| 画布 | 暖灰 `#ECE7E1` |
| 主文字 | `#4A4239` |
| 参考辅助色 | `#8A7F73` |
| 主强调 | 暖橙 `#ECA54D`；小字采用更深的 `#BD7C2E` |
| 故障强调 | 珊瑚 `#BF7566`；状态辅助绿 `#7EAC68`、蓝 `#5B7FB5` |
| 边框 | 弱化硬边框；20px 圆角、右下 `#D6CFC6` 阴影和左上 `#FBF8F4` 高光 |
| 标题 | 约29px、650 字重；中文信息优先 |
| 字体 | 沿用项目已有 Geist / 中文系统字与等宽数值；未增加参照页的在线字体依赖 |
| 组件 | 浮雕卡片、内凹选中按钮、圆角搜索框、无描边导航与真实状态环 |

工作台六面板为故障、上下文连续性、采集记忆、交付物、文件历史和客户端接线。环形视觉中央是原采集事件计数，不是质量分或完成百分比；其他数值来自原 API。宽桌面使用参照的大小错落卡片：左侧主卡、中央小卡、右侧状态卡；窄栏两列，手机单列。其余两页保留原组件和流程，通过共同主题与顶部导航统一。

## 标识与素材

`contextrail/ui_static/contextrail-logo.png` 原样采用用户在本聊天请求生成并随后指定使用的透明 PNG。图形为深绿双轨、铜色连接点。通过 CSS 安全框适配页头，未重绘或改色。原始生成文件保留在本机 generated_images，项目内版本供三页页头与 favicon 共同使用。它是位图，不是交付 SVG。

共用主题为 `contextrail/ui_static/context-theme.css`，本地字体为 `contextrail-font.woff2`，许可沿用已交付 Geist-OFL.txt。三页使用同源静态资源。原生 Codex/Claude Agent 的平台标识仍使用其原组件，不与 ContextRail 产品标识混同。

## 维护边界

2026-11-04 用户以 Maple Ridge House 截图进一步明确“这个风格的版本”。当前工作台、Bridge 和共用主题按该图修复。ContextRail 与 Context Bridge 是两个独立界面，使用各自品牌页头、功能导航、URL和桌面入口；只统一视觉形式。业务功能保留，Bridge 桥形图标继续为透明底。

变更集中于静态主题、工作台 HTML/展示组织、My Work 品牌和导航、Viewer Header/HTML。原后端、Hook、注入、任务/记忆库和设置写入接口不变。本次未新增依赖、模型调用或常驻服务。构建继续使用 `ui/claude-mem/UPSTREAM.md` 的两个现有生产构建入口，主题和资产通过原静态包包含规则交付。

## 固定页头与屏幕空间

三页通过rail-shell.js共用一个固定导航壳，位于各自React根节点之外；导航在加载与页面滚动时保持，原主题/来源控件保留在工具槽。导航改为参照的圆角浮雕按钮，选中态使用内阴影；品牌副标题为PERSONAL CONTEXT SYSTEM。页头桌面112px、窄屏152px，内容随窗口自然滚动，卡片长内容有界滚动。记忆时间线与会话卡片独立网格：1200px起三列、761–1199px双列、760px及以下单列，保留逐行时间顺序。
