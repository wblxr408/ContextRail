# 命中窗口与原文锚点

C05让任务全文候选和已保存命令的原始两流检索返回真正围绕词位的上下文。已有hit.ref保留原结构跨度；hit.windows中的每个ref都准确对应其text，hit.snippet_ref对应显示的snippet。它们都是同一原始name/revision中的绝对UTF8字节区间，可直接交给context.get。

```powershell
.venv/Scripts/python.exe -m contextrail search-indexed --workspace D:/my-project --task my-task --session owner --query '错误记录' --context-chars 80 --window-limit 3 --response-bytes 8192
.venv/Scripts/python.exe -m contextrail result-search --workspace D:/my-project --task my-task --session owner --result-ref '{"name":"tool-result/build-1/result","revision":1}' --query 'failure' --context-chars 40 --window-limit 2 --response-bytes 8192
```

只读MCP的context.search_indexed与context.result_search接受相同可选context_chars/window_limit。前者绑定当前任务证据，后者仍限制为指定结果的stdout/stderr，并保持execution_status与检索状态分别表达。原格式的调用不需要新增参数。

每个hit新增：

| 字段 | 含义 |
|---|---|
| title/title_truncated | Markdown标题/Python结构path，普通段或日志则为首个命中所在行的有界展示 |
| match_origin | content表示正文词法命中；source_label表示只命中名称/路径，不伪造正文词位 |
| windows | 文本、精确ref、全局line_start/line_end、实际matches及其计数 |
| matches | 每个term、真实词位ref及全局1起始line；casefold只用于匹配，不改变原文 |
| windows_total/windows_omitted | 本结构段生成的窗口数和未展示数 |
| matches_total/matches_omitted | 真实词位数和没有在任何展示窗口中显示的词位数 |
| snippet_ref | 预览文本的精确引用，不能把预览字符下标当成原文byte offset |

CRLF按一次换行计数，独立CR或LF也计换行；保留原换行和emoji字节，不做换行归一化。行范围包含窗口实际最后一个字节所属行；窗口末尾的换行属于前一行。上下文只在当前FTS结构跨度内扩张，原结构ref仍能完整回读。超出8KiB分块的词法边界限制沿用C03。

默认每侧最多80字符（0..256）、每候选最多3窗口（1..8），单窗最多2048字节，每窗最多32词位。同段相邻/重叠窗口在该字节上限内合并；远距离窗口保留。窗口按来源位置顺序展示，省略数明确；可以提高window_limit、缩小查询或按结构ref回读。相同标题的不同字节区间保持独立，标题不是去重身份。

正文无词位的名称/路径候选展示来源开头片段，matches为空、matches_total=0。正文词位遵守既有完整词法token和中文bigram，不把名称查询误当成正文子串匹配。最长可索引token仍完整显示在窗口中，上下文按UTF8边界缩短。

完整JSON仍按budget约束，超额省略整条hit，保留total/omitted与原候选分页。省略所有hit时同offset增加response budget，不跳过尚未展示的候选。候选总数统计结构跨度，windows_total统计该跨度局部窗口；两者不是同一个计数。

窗口不读取代码依赖、不执行源码、不选择额外工件。代码依赖仍由宿主的SelectionGroup表达。当前消费者、真MCP与实际任务回读证据见openspec/changes/precise-hit-windows/verification.md。
