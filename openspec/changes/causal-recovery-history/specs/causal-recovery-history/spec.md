## ADDED Requirements

### Requirement: Source-bound error attempts
系统 SHALL 保存失败来源、明确处理声明与同会话验证结果，并按错误类别回读。

#### Scenario: Adjacent success
- **WHEN** 一个命令失败后相邻的其他命令成功
- **THEN** 原失败仍未确认解决，成功不自动关联为修复

#### Scenario: Bound verification
- **WHEN** 宿主预先绑定验证 call 并收到其完整实际结果
- **THEN** 只更新该处理的验证结果，保留失败与处理声明的来源；不完整结果保持 unknown

### Requirement: Exact read-only native recovery
系统 SHALL 只读指定 checkout/client/session 的可见 user/final/工具结果，保留精确范围和损失信息。

#### Scenario: Interrupted without handoff
- **WHEN** 最后回合没有 final 且原生文件有未完成末行
- **THEN** 可读最后真实请求和已完成工具结果，提示缺口，不解析隐藏推理或输出凭据

#### Scenario: Wrong source or changed cursor
- **WHEN** 来源 checkout/session 不符、多个来源有歧义或游标物理文件被替换
- **THEN** 拒绝冒充原会话完整恢复，不自动选择其他来源

#### Scenario: Bound reader and export
- **WHEN** 模型读已绑定原生会话或宿主导出 Bridge 材料
- **THEN** 模型不能覆盖源范围，导出带来源/缺口且不改当前目标、日志或原 M 游标
