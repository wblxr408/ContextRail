# 已保存输出的确定性提取

B03 在原始 stdout/stderr 已经由 capture-result 保存后工作。它读取精确 name/revision，返回统计、筛选条件和样本的原文字节跨度；不会执行命令、读取任意路径或修改证据。输出明确标为 derived_extraction_with_source_spans。

## MCP / Python 工具

两端既有 MCP 配置在新会话列出 `context.extract`。它与 ContextTools 使用相同绑定：

```json
{"source":{"name":"tool-result/call-1/stdout","revision":1},"kind":"json","options":{"path":"/events","where":{"path":"/level","equals":"error"},"select":"/message"},"limit":20,"budget":4096}
```

source 必须是完整既存 revision（start=0、end=null），读取历史版本也显式给 revision。任务和会话由宿主固定，参数中不允许 scope、文件路径、命令或表达式。MCP 服务继续使用原来的源码入口，无需第二个服务。

每条 item 的 `source.ref` 可以直接交给 `context.get`，读到原始行、记录或 JSON 值；该 ref 的 start/end 是左闭右开 UTF-8 bytes，保留原 CRLF、引号、转义和空白。它不是重新序列化的文本位置。

## 提取类型

| kind | options | 返回与定位 |
|---|---|---|
| log | `{}` | 按 ERROR/FATAL/CRITICAL/Traceback/*Exception 固定词标记匹配物理行；相同完整消息聚合并计数，每组第一条原行可回读。不同时间戳/数字不自动合并。 |
| json | path、where、select，均可选 | path 定位值；数组按元素处理，where 为相对 Pointer 与 equals 标量类型严格相等；select 是相对 Pointer。每个选中值附精确 Pointer 和原文跨度。 |
| csv | numeric 列名数组、delimiter（默认逗号） | 首行表头，支持标准引号和多行字段；numeric 分别报告有效数/空白/无效数、sum/min/max/mean。样本带记录序号及物理起止行。 |
| git_status | `{}` | 仅 `git status --porcelain=v1 -z`，返回 index/worktree/path，重命名保留 original_path。 |
| git_numstat | `{}` | 仅 `git diff --numstat -z`，返回 added/deleted/path，重命名保留原路径；二进制计数为 null，binary_files 单列统计。 |

JSON 路径采用 [RFC6901 Pointer](https://www.rfc-editor.org/rfc/rfc6901)：`~1` 表示 `/`，`~0` 表示 `~`，空字符串表示当前值。没有 JSONPath、脚本或自由表达式。无匹配是 no_match；重复对象键、NaN/Infinity、非法转义/残缺输入不会静默纠正。CSV 空/重复表头、行宽不一致、无法解析的引号均明确失败；没有自动猜方言。

CSV 数值使用 Decimal，sum/min/max 输出十进制文本；mean 使用 34 位有效数字及 ROUND_HALF_EVEN。超过 1000 位有效数字或指数绝对值大于 1000 的单元格计为 invalid，NaN/Infinity 同样无效，不会伪装成0。Git 字段协议见 [status](https://git-scm.com/docs/git-status)、[diff](https://git-scm.com/docs/git-diff)；-z 模式的空格、tab、换行路径不会按空白拆开。

## CLI

使用与 capture-result 相同的 workspace、task、session、store/project-id 覆盖参数：

```powershell
.venv/Scripts/python.exe -m contextrail extract-result --workspace D:/my-project --task my-task --session owner --source '{"name":"tool-result/call-1/stdout","revision":1}' --kind csv --options '{"numeric":["amount"]}' --response-bytes 4096
```

这个命令只读取已经保存的证据；原始命令由宿主执行。日志和 Git 类型可省略 --options。

## 状态、预算与边界

完整返回 JSON 遵守 budget/response-bytes，上限受绑定 reader 的 read_bytes 限制。先扫描完整输入，再限制展示项（默认20、最多100）；matched/returned/omitted 区分匹配总数、展示数和省略数。过大的单项整体省略，统计不改，source 完整引用仍可回读。连元数据也装不下时返回 budget_exceeded。

parse_failed 表示未能完整解析，scan.complete=false，原文保持原引用；不会把前半段统计冒充全量。输入最大16MiB、JSON最多50000节点/128层、文本/CSV/Git最多50000记录；超限为 incomplete/resource_limit。UTF-8 无效输入同样保留原始二进制，context.get 可按 base64 回读。JSON 值用标准解码类型，精确十进制字面仍以来源跨度为准。

本批没有目录索引、自动格式猜测、意图检索或模型摘要。日志词规则是确定性启发式，不是“发现全部异常”的保证。实际消费者和 MCP 证据见 openspec/changes/deterministic-output-extraction/verification.md。
