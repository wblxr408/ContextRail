# Spec Delta

## Purpose

将任务词法检索和已保存原始输出的候选转换为围绕真实命中位置的上下文窗口，提供中文、emoji及原换行下的精确字节引用和全局行号，使调用者能回到同一来源版本核对原文而无需猜测预览截取位置。

## ADDED Requirements

### Requirement: Exact source windows and anchors
系统 SHALL 返回来源版本、结构标题、窗口文本/字节ref及命中词位的字节ref和全局行号。MUST 保留UTF8/CRLF原字节，窗口可经原文工具逐字读回；仅路径/名称匹配不得伪造正文命中。

#### Scenario: Chinese hit in middle of a long paragraph
- **WHEN** 中文词出现在长段落中部，前文含emoji和CRLF
- **THEN** 预览围绕命中，窗口和词位各自回读到相同原文，全局行号正确。

### Requirement: Merge proximity without losing source identity
系统 SHALL 合并同一结构段中重叠或相邻的命中窗口，对远距离命中保留不同窗口。相同标题的不同原文字节区间 MUST 保持独立身份，代码依赖仍由宿主SelectionGroup决定。

#### Scenario: Repeated headings and nearby terms
- **WHEN** 两个同名标题各有命中，且一个段内有相邻的多个词
- **THEN** 同名段不互相去重，相邻词共享窗口但各自词位仍精确，远距离窗口独立。

### Requirement: Bounded windows with declared omissions
系统 SHALL 限制上下文字符、单窗口字节、每候选窗口与词位数，并声明未展示的窗口/词位。MUST 返回完整JSON；预算不足按整项省略并保留分页/原结构回读入口，不能声称展示了全部命中。

#### Scenario: Narrow response budget and many matches
- **WHEN** 同段含大量重复命中或响应预算很小
- **THEN** 窗口/词位保持有界，省略计数可见，预算不足可重试同offset或精确读结构引用。

### Requirement: Shared read only retrieval surfaces
系统 SHALL 让CLI、只读MCP任务检索与原两流检索共用精确窗口行为，保持绑定任务/版本和既有执行状态。MUST 不借窗口生成读取其他任务或扩张为代码执行/依赖加载。

#### Scenario: Original failed command output
- **WHEN** 对已保存失败命令的stderr查询词位并回读窗口
- **THEN** 执行失败状态保持，窗口来源仍为该流真实字节，其他任务内容不混入。
