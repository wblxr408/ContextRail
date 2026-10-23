# Spec Delta

## Purpose

把已保存的任务工具结果与显式归入任务的原生事件整理成可回读的经历视图，并为原事件库提供精确会话查询。使用户能定位错误前的尝试、后续回应及其发生时间，避免把采集时间或派生判断伪装成原事实。

## ADDED Requirements

### Requirement: Honest chronological browsing
系统 SHALL 按来源的实际发生时间排序，无有效时间的事件置后且标明未知，不能用观察时间代替发生时间。

#### Scenario: Late arrival and missing timestamp
- **WHEN** 来源乱序到达且某条没有时间
- **THEN** 有时间的按实际时间排序，无时间单独置后且原时间状态可见。

### Requirement: Scoped filters and neighborhood
系统 SHALL 支持绑定task、session、文件、角色、来源、错误类别及时间范围过滤，以及围绕精确事件浏览前后经历。

#### Scenario: Error followed by correction
- **WHEN** 用户选定错误并请求前后窗口
- **THEN** 返回范围内先前尝试和后续纠正，各项带可回读证据，其他task或会话不混入。

### Requirement: Recoverable evidence and derived labels
时间线 SHALL 保留每项精确原证据引用；错误类别 MUST 明示派生规则及其依据，不更改原始exit code或宣称因果。

#### Scenario: Category is only a heuristic
- **WHEN** 失败结果文本匹配某错误类别
- **THEN** 结果给出类别、分类来源和原始状态，用户可打开完整正文核对。

### Requirement: Bounded queries expose gaps
查询 SHALL 限制条数、完整响应字节与补建资源；旧来源未索引、来源不可用和正文省略 MUST 明确报告，不能把不完整视图说成完整经历。

#### Scenario: Existing sources await indexing
- **WHEN** 旧库只有部分来源已建时间索引
- **THEN** 返回已有结果并标pending缺口，显式补建可继续，读取不隐式扫描或改写全部原文。
