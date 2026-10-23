# B03 验收

2026-10-22，Windows / Python 3.11，当前变更 `deterministic-output-extraction`。

## RED → GREEN

消费者首先通过现有 OutputRecorder 保存包含中文、emoji、CRLF 的日志，再调用 CLI extract-result，断言退出0、错误聚合数及精确样本。初次 fixture 少传 create_task 的 allowed_providers，是夹具错误，不计行为 RED；修正后观察到退出2与缺少 extract-result 的断言失败，作为功能 RED。

实现提取器、绑定 ContextTools 和 CLI 后，首次整批暴露夹具手写 Scope.branch 与项目解析的 worktree ID 不同，导致 CLI/MCP 找不到 task。夹具随后通过现有 load_project 取得真实身份，未放松项目/任务隔离。

首次当前用例7项通过后，数值边界复核新增极大值与微小数相加用例。原2200位 Decimal 上下文丢失 1e-1000，观察到实际 RED；按支持的1000位有效数字/±1000指数及最多50000行计算，累加精度改为3100位，保留完整sum，mean仍单独按34位有效数字舍入。

最终运行 `.venv/Scripts/python.exe -m unittest tests.test_output_extraction`：**8 项通过，3.069s，无跳过**。

| 当前需求 | 实际输入与观察 |
|---|---|
| 日志聚合与定位 | CLI 读取保存的中文/emoji/CRLF 日志：3条错误、2组，第一组count=2，context.get 回读完整原行。 |
| JSON 路径/筛选 | /rows + bool严格筛选 + /a~1b/~0name，排除数字1，返回中文/转义换行/emoji值；样本读回带原引号的JSON片段；无匹配独立状态。 |
| CSV 统计/多行定位 | 引号内CRLF记录准确定位到物理2–3行；0.1+0.2=十进制0.3，mean0.15，非数值与空白单独计数。 |
| CSV 数量级边界 | 最大支持整数位范围与1e-1000相加保留末尾非零位，不因mean或默认Decimal上下文把sum提前舍入。 |
| Git 字段 | porcelain-v1 -z重命名含tab/换行路径不拆坏；numstat -z重命名与binary '-'保留语义，计数不把binary当0。 |
| 格式失败保留来源 | 重复JSON键、NaN、未闭CSV引号、重复表头、缺rename路径、缺最终NUL及无效UTF8均parse_failed；原文逐字节仍可从原ref读取，任务版本不改。 |
| 预算/历史版本 | 超大单条日志在1800-byte完整JSON预算下整体省略，扫描统计仍全量；明确读revision1即使已有revision2；极小预算拒绝、非法Pointer与task override拒绝。 |
| 真实MCP | 官方 SDK ClientSession 启动真实stdio server，调用 context.extract 后按返回source.ref读取 ERR-492 原始对象；command override拒绝。 |

原始 capture 输入是受控宿主观察夹具，提取路径使用真实 Store、ContextTools、CLI/SDK stdio，没有外部模型参与，也没有伪称跑过生产命令或检测出所有错误。

## 边界

统计派生结果只读不落第二套记忆。输出有完整source revision；原始数据和长期 main 仍分别归既有 Store/Basic Memory。输入和节点/记录上限在文档明确，解析未完成不展示部分统计冒充全量。JSON浮点值使用标准类型，精确字面以源span为准。

当前只运行本变更8项消费者用例；没有执行旧批次/全项目回归、smoke、哈希比较或启动探针。代码未提交/推送。strict与git diff --check通过；wheel构建通过，沿用离线非隔离命令与Anaconda解释器，已有bdist_conda入口警告非致命。数值精度修复后再次构建并记录结果。

数值精度修复后的源码已重新构建成功。
