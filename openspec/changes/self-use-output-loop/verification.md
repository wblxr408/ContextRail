# 首批实现验收（2026-10-22）

## 范围与结果

A01、A02、A04、A05、B01、B02、B05、B07 已实现。操作指南和输入契约在根目录 SELF_USE.md；项目模板为 .contextrail.example.toml。TODO 只勾选这八项。此结果是可信宿主接收器与只读 MCP 接口，不表示已为个人 Codex/Claude 安装 Hook 或替换聊天历史；双端接线仍为 A06。

## 行为证据

新增消费者契约文件 tests/test_self_use_output_loop.py。首次 RED：project-info/project-init 的合法调用返回 2，预期 0，原因是尚无新增 CLI 入口。MCP 实调用暴露 SDK 默认校验返回自由文本错误，改为复用 ContextTools 严格参数校验，保留稳定 JSON error；忽略来源用例首次 RED 为 invalid_request 而非预期 access_denied，接通 source_paths/ignore 规则后 GREEN。

收尾复核新增了超大整数时间戳用例：首次 RED 为 CLI 返回未捕获异常而非 JSON 错误。接收器现将数值转换溢出归为 invalid_request，且不写入证据。

最终命令 `.venv/Scripts/python.exe -m unittest tests.test_self_use_output_loop`：11 项通过，10.448 秒，无跳过。最后一次执行含如下实际结果：

| TODO | 可观察效果 |
|---|---|
| A01 | 子目录 marker、相对库路径、临时 task 覆盖和来源报告；ignored source 整条拒绝、不增任务版本 |
| A02 | 同名非 Git 目录隔离；linked worktree 共 repo、分 scope；Windows 大小写及真实 junction 共身份并保留展示路径 |
| A04 | 官方 mcp 1.30.0 ClientSession 连接真实 stdio 子进程，读取历史 revision、当前快照两页及错误后继续读取；拒绝另一任务和 scope 参数注入 |
| A05 | read supported，其余宿主控制 unknown；不报告已替换历史 |
| B01/B02 | 实际 Python 子进程产出大中文/emoji/CRLF stdout、独立 stderr 并 exit=1；宿主以 base64 提交，保存信封和两路原文，经 MCP 取回末尾 TAIL-live-7429 和真实 exit/source_session |
| B01 | 超出浮点表示范围的时间戳返回稳定 invalid_request，任务版本仍为零 |
| B02 | 重新打开 SQLite 完整恢复原始两路 bytes；同 call ID 重送不增加 version；异内容冲突；已有 stderr 冲突时无 stdout/信封残留 |
| B05 | 完整可解析 JSON ≤1536 UTF-8 bytes（不含行分隔符）；原文与末尾精确；binary 保留；32-byte 预算明确拒绝且不入库 |
| B07 | rg/grep 无匹配、带 stderr 的搜索失败、输出普通文本的构建失败、包装 shell、timeout、partial、unknown、success 分类与原 rc 正确 |

## 构建与规范

- `openspec.cmd validate self-use-output-loop --strict`：通过。
- `git diff --check`：通过；Git 提示既有 CRLF 转换，不是失败。
- `uv build --wheel --offline --no-build-isolation --out-dir dist --python C:/Users/wblxr/anaconda3/python.exe`：成功生成 dist/contextrail-0.1.0-py3-none-any.whl。Anaconda bdist_conda 入口有非致命警告；wheel 构建退出 0。
- 初次 `.venv` 的 pip wheel 因未装 pip 失败；uv 隔离构建/下载未结束而取消；离线无隔离构建因缺 setuptools 失败；离线补装缺缓存，在线补装取消。最终复用已有 Anaconda 构建后端完成，无新增核心依赖。

## 证据边界

旧回归套件未运行，没有完整回归结论。未启动当前桌面会话动态 MCP 注册、未写个人客户端配置、未修改安装中的 context-bridge 或长期记忆监听器。SDK 客户端证明真实 MCP 数据调用；不冒充个人桌面接线。context.required 为声明材料，自动读取与注入属于后续条目。capture.ignore 只能检查 cwd 与如实声明的 source_paths，无法从任意 stdout 推断全部来源。构建来自当前工作树，含用户原有未提交研究改动，未发布。
