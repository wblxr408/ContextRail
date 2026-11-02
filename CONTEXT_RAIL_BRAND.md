# ContextRail 本地界面视觉

本轮按用户明确指定的 [Kombai MedPanel](https://kombai.com/gallery/inspirations/dashboards-web-26/) 适配现有工作台、My Work 和记忆档案。已查看 Gallery/Web、完整说明及[实际预览](https://agent.kombai.com/kombai-inspiration-library/app-pages/dashboards/web/26/index.html)，参考 ID 为 `kombai:import-screen:dashboards-web-26`。

## 设计落点

| 项目 | 当前应用 |
|---|---|
| 画布 | 金奶油 `#FCF2DC` |
| 主文字 | `#201D16` |
| 参考辅助色 | `#908670`；本地小字为可读性用 `#827966` |
| 主强调 | 蓝绿 `#2D6B72` |
| 故障强调 | 珊瑚 `#D56857` |
| 边框 | 棕灰 `#B7AD98`，细直描边与内框 |
| 标题 | 26px、600 字重；中文信息优先 |
| 字体 | 已有 Geist / 中文系统字与等宽标签；不分发参考页的 Söhne、Berkeley Mono 字体 |
| 组件 | 文件夹页签、紧凑列表、框线按钮、语义色标、六面板与文件路径查询条 |

工作台六面板为故障、上下文连续性、采集记忆、交付物、文件历史和客户端接线。环形视觉中央是原采集事件计数，不是质量分或完成百分比；其他数值来自原 API。正常桌面三列，窄栏两列，手机单列。其余两页保留原组件和流程，通过共同主题与顶部导航统一。

## 标识与素材

`contextrail/ui_static/contextrail-logo.png` 原样采用用户在本聊天请求生成并随后指定使用的透明 PNG。图形为深绿双轨、铜色连接点。通过 CSS 安全框适配页头，未重绘或改色。原始生成文件保留在本机 generated_images，项目内版本供三页页头与 favicon 共同使用。它是位图，不是交付 SVG。

共用主题为 `contextrail/ui_static/context-theme.css`，本地字体为 `contextrail-font.woff2`，许可沿用已交付 Geist-OFL.txt。三页使用同源静态资源。原生 Codex/Claude Agent 的平台标识仍使用其原组件，不与 ContextRail 产品标识混同。

## 维护边界

变更集中于静态主题、工作台 HTML/展示组织、My Work 品牌和导航、Viewer Header/HTML。原后端、Hook、注入、任务/记忆库和设置写入接口不变。本次未新增依赖、模型调用或常驻服务。构建继续使用 `ui/claude-mem/UPSTREAM.md` 的两个现有生产构建入口，主题和资产通过原静态包包含规则交付。

## 固定页头与屏幕空间

三页通过rail-shell.js共用一个固定导航壳，位于各自React根节点之外；导航在加载与页面滚动时保持，原主题/来源控件保留在工具槽。桌面页签宽88px，品牌副标题统一为PERSONAL CONTEXT SYSTEM。工作台在至少1051×680视口使用三列两行填充剩余屏幕，详情在面板内滚动；更小窗口自然滚动。记忆时间线与会话卡片独立网格：1200px起三列、761–1199px双列、760px及以下单列，保留逐行时间顺序。