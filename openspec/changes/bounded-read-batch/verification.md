# B06 验收

2026-10-22，Windows/Python3.11。本轮仅实现独立只读批处理。

## RED → GREEN

消费者先调用 batch-read CLI，期望按序返回成功、not_found、日志提取。fixture 最初把 expires_at 写为 expires，是夹具错误，不计功能RED；修正后观察CLI缺命令、退出2的真实断言失败。实现CLI、ContextTools、只读连接和线程池后通过。

随后补充过深请求与提取失败边界：1200层请求曾泄漏RecursionError，修复为调度前invalid_request；提取器parse_failed曾被外层标为succeeded，实际断言RED后改为failed/invalid_format，保留完整来源结果，兄弟读取仍成功。

最终 `.venv/Scripts/python.exe -m unittest tests.test_read_batch`：**8项通过，2.510s，无跳过**。

| 当前契约 | 实际观察 |
|---|---|
| 输入顺序与独立失败 | CLI批量读中文emoji/CRLF、缺失revision、日志提取，按三个原id返回成功/失败/成功；任务状态和版本不变。 |
| 真正有限并发 | 两个真实Store读取在注入的expiry clock处通过Barrier同步，观察同时在途2，无时间阈值/固定sleep；结果原文一致。 |
| 连接只读 | 实际 mode=ro/query_only Store连接执行put被SQLite拒绝；worker不运行默认构造的初始化/迁移。 |
| 整体预算 | 大成功结果整体省略，1800byte完整JSON内保留成功状态、request与兄弟not_found；budget=1在读clock可触发前拒绝。 |
| 调度边界 | 重复id、写动作、depends_on、concurrency=9、宿主活动事务拒绝；内存库concurrency=2拒绝，显式1读回原内存字节。 |
| 深输入 | 1200层JSON参数以InvalidRequest拒绝，没有开始读任务。 |
| 语义失败 | 无法解析的JSON提取被标为failed/invalid_format，原source保留，另一项正常读取成功。 |
| 真实MCP | 官方SDK真实stdio调用context.batch，中间not_found不阻止其他结果；再按日志提取source.ref单项读取原始ERROR行。 |

## 边界

每项read snapshot独立，不能把有界并发说成整批原子或CPU加速。仅get/extract可批量，授权和写入不进入池。输入与整体/单项输出预算分别限制，省略是交付状态，不修改证据。没有启动新daemon。

没有执行以前批次/全项目回归、smoke、哈希或启动探针。构建、strict与git diff --check使用实际结果记录；源码仍本机未提交/推送。

最终 `openspec.cmd validate bounded-read-batch --strict` 与 `git diff --check` 通过；当前源码 wheel 离线非隔离构建成功。命令沿用 `uv build --wheel --offline --no-build-isolation --out-dir dist --python C:/Users/wblxr/anaconda3/python.exe`；bdist_conda缺模块警告仍非致命。
