# Spec Delta

## Purpose

让用户把明确工作区内的源码和文档按范围与资源限制导入任务证据，部分扫描可以使用持久游标继续。内容变化产生新版本，完整扫描确认的删除才失效，原始来源、历史版本与记忆队列的未完成状态保持可核对。

## ADDED Requirements

### Requirement: Scoped bounded directory capture
系统 SHALL 支持include/exclude、原生gitignore、深度、每批文件数、单文件体量限制，并默认跳过依赖/生成目录和链接。MUST 不读取越界目标或执行源文件，UTF8/CRLF原字节保留，非文本及未覆盖范围明确。

#### Scenario: Repository with nested rules and links
- **WHEN** 工作区含嵌套gitignore、重新包含规则、依赖目录和指向外部的链接
- **THEN** 仅导入符合选择的范围内文件，外部链接/生成依赖不进入证据，条目与排除原因可查。

### Requirement: Durable exact continuation
系统 SHALL 保存绑定任务/来源的run游标和版本，部分扫描提供明确继续入口；资源上限可提高后续读。旧/错任务/被替代run或并发重复推进 MUST 拒绝，失败提交不推进游标。

#### Scenario: Resume after file limit
- **WHEN** 首批到达文件上限并退出进程
- **THEN** 新进程可按精确run继续，已保存内容不重复产生revision，未读内容仍可见为partial。

### Requirement: Incremental revisions and conservative deletion
系统 SHALL 将实际内容变化映射为新revision，相同内容不重复保存；完整扫描后才失效未再观察到的旧来源。MUST 保留历史ref的明确状态，仅影响绑定任务/来源，分页/错误/深度受限时不得猜测删除。

#### Scenario: Edit delete and reappear
- **WHEN** 已导入文件被修改、删除后又重新出现
- **THEN** 当前候选跟随新版本或删除终态，旧revision可区分历史/不可用，其他任务与目录来源不受影响。

### Requirement: Explicit existing memory queue
系统 SHALL 提供把已保存文档版本显式送入原工程记忆jobs队列的入口，复用既有清理/分片/队列语义；不得默认启动新观察者或把目录全量转长期知识。同一source revision重送 MUST 只有一次队列业务效果，状态仍为queued_not_yet_classified。

#### Scenario: Retry explicit document enqueue
- **WHEN** 同一导入run的文档重复送入已有队列，或磁盘文件在Rail捕获后已变化
- **THEN** 队列使用已保存版本而非悄悄重读新文件，重送不重复入队，失败/不支持文档类型明确。

### Requirement: Bounded observable progress
系统 SHALL 通过CLI和只读MCP提供明确run状态、条目分页、来源版本和完整JSON预算；部分目录覆盖不得被全文索引的空结果隐藏。

#### Scenario: Source still partially scanned
- **WHEN** 索引已观察到的文件均完成，但目录run仍有未处理项
- **THEN** 查询覆盖仍声明目录未完成，不能把未导入的文件断言为无匹配。
