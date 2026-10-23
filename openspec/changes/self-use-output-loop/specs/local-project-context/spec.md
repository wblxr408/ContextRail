# Spec Delta

## Purpose

为本机自用任务解析可信项目配置，并把仓库、工作树、任务与客户端会话分别标识，使目录重名和并行工作树不会混合状态，且临时覆盖具有明确作用范围与来源。

## ADDED Requirements

### Requirement: Explicit project configuration

系统 SHALL 解析项目 TOML、相对于 marker 的路径和显式临时覆盖；未知字段或非法预算 MUST 拒绝，且覆盖不得修改配置文件。

#### Scenario: Temporary task override
- **WHEN** 在子目录加载 marker 并显式覆盖 task
- **THEN** 使用相同工作区与库路径、使用新 task，报告参数来源，原 marker 保持原值。

### Requirement: Separate repository and worktree identity

系统 SHALL 分别标识 repo、worktree、task 与 session；配置显式 project ID 可覆盖 repo 标识，仍不得合并两个工作树的任务状态。

#### Scenario: Linked worktrees
- **WHEN** 同一 Git 仓库的两个工作树加载配置
- **THEN** repo 标识相同，worktree 及对应 Scope 不同；客户端 session 不进入 repo 身份。

#### Scenario: Same named directories
- **WHEN** 两个非 Git 目录同名但绝对位置不同
- **THEN** 它们的默认项目及任务状态分离。

### Requirement: Declared capture exclusions

系统 SHALL 在保存宿主结果前核对 cwd 和声明的 source_paths；越出工作区或命中 capture.ignore 时 MUST 拒绝整条结果。自由文本中的来源未知时，不得宣称已经检查过所有文件访问。

#### Scenario: Ignored source
- **WHEN** 宿主声明来源 private/credentials.txt 且配置忽略 private/**
- **THEN** 返回拒绝，任务版本与证据保持原状。
