# Spec Delta

## Purpose

在原生客户端未完成回复或异常终止时，仍保留可恢复的用户请求与过程证据。将用户、工具调用、工具结果、助手回复及生命周期分别定位，来源身份、角色与时间不因知识归纳而混淆，并让选定事件能进入当前任务的精确回读体系。

## ADDED Requirements

### Requirement: Persist requests before completed replies
系统 SHALL 在完整用户记录到达时保存完整处理后请求证据，不依赖final或知识候选；证据和读取游标 MUST 同事务提交，失败后重读不能跳过或重复产生效果。

#### Scenario: Crash without final
- **WHEN** 客户端仅写入用户请求后退出且没有助手final
- **THEN** 重开服务仍可按原workspace/session读回请求，知识队列没有因此新增事实候选。

### Requirement: Preserve native event distinctions
系统 SHALL 分别保存user、tool call、tool result、assistant及lifecycle的处理后数据和原生session/turn/message/call关联。MUST 不将Claude的tool_result当作用户新请求，不捕获thinking/reasoning正文，不以回复文本定义来源身份。

#### Scenario: Tool followed by final
- **WHEN** 同一工具有调用及返回，随后出现最终回复
- **THEN** 三类事件可独立回读并按call/turn关联，原始事件不会自动转为同一知识事实。

### Requirement: Honest time and bounded evidence
每个事件 SHALL 分别保留发生时间和接收时间；来源时间缺失、无时区或无效时 MUST 显式标记，不用接收时间伪装。已知凭据 SHALL 在保存和预览前处理；超限或不可采集块 MUST 有省略状态，不把省略文本当完整原文。

#### Scenario: Missing source timestamp
- **WHEN** 原生记录未提供有效有时区时间
- **THEN** 发生时间保持未知或有歧义，接收时间单独可查，事件仍按采集顺序定位。

### Requirement: Scoped task import and exact reread
可信宿主 SHALL 显式选择workspace、client及native session导入已有事件；目标任务写入 MUST 遵守lease/freeze并原子发布完整证据。导入重试 MUST 复用原事件，回读范围属于保存的处理版，不能混入其他workspace/session。

#### Scenario: Import and reread request
- **WHEN** 将选定session的用户请求和工具结果导入当前任务
- **THEN** 现有只读工具按精确revision回读各事件及来源映射，错误租约/冻结不发布半份证据。
