# 工作现场与在途动作（E02 / E03 / B08）

继续使用原 M 捕获、R task_state/actions 和 Bridge STATE。宿主实际执行命令；此模块不执行命令、不自动重跑，也没有新增默认监听器或后台执行服务。

## 已接通的来源

原 M 新捕获的脱敏事件增加 `scene` 候选，旧历史保持原样。R 原 `event-import` 选定 workspace/client/native_session 后，自动按 call ID 关联调用与结果，跨页或结果先到可重放；不同 session 的同名 call 不配对。原 `OutputRecorder.capture` 保存宿主真实结果时自动记录工作现场，同名动作的确定终态也同步到原 actions。

文件路径/范围、cwd、Git操作、环境操作保留原 Ref；请求、已观察、失败、未知分开。Git调用不等于成功；只有来源回执包含退出0和提交回执时才提取 `source_reported` 提交ID，不把它冒称当前仓库HEAD。任意shell脚本只保留请求，不猜测其文件副作用。

宿主结果可额外提供 `environment`，仅接收 executable/virtual_env/prefix/base_prefix/worktree/host_executable/host_prefix/host_base_prefix 字符串。它是宿主声明的实际环境，绑定原结果；命令argv中的解释器另标 `requested_argv_not_resolved_environment`，两者不混用，不采集整份环境变量。

这是所选来源的投影。原M继续捕获，R只转换已显式导入或提交的材料；没有自动导入所有历史或把模型只读查询变成状态写入。捕获过滤后的正文/路径不会从原始封包补回来。

## 任务与完成证据

TaskCreate/TaskUpdate/TodoWrite/update_plan 的 completed 是 `completion_claim`，保留 inferred；没有结果不升级真实完成。中文显式 `任务[ID]：`、`计划[ID]：`、`阻塞[ID]：`、`放弃[ID]：`、`完成[ID]：` 可转换。用户放弃和阻塞作为来源声明保留，助手推断标明；普通“已经搞定”不解除阻塞。

```python
from contextrail.workscene import WorkScene
scene = WorkScene(store, scope, lease, workspace)
scene.declare("build", "blocked", "等待构建结果", source_ref,
              expected_call_id="build-42")
# 原宿主实际执行并用 OutputRecorder 保存 call_id=build-42 的结果。
scene.complete("build", "build-42")
```

绑定必须在结果之前建立；其他call、失败、partial或timed_out结果不能完成该任务。旧状态保留为superseded，原来源仍可回读，重复完成不产生第二个状态效果。无ID自由文本不跨语义合并。

## 显式在途动作

```python
receipt = scene.reserve("build-42", argv=actual_argv, source=source_ref,
                        background=False)  # True/False 均须明确
# 只有 execute_once=True 才由原宿主执行；False 时回读，不能重跑。
scene.started("build-42", host_session=str(actual_host_session), pid=actual_pid)
scene.partial("build-42", stdout=observed_stdout, stderr=observed_stderr,
              part_id="chunk-1")
state = scene.recover("build-42")
```

登记 live PID 时绑定创建时间，不能只凭PID恢复或终止。进程太快已结束时保留原宿主session，最终用真实结果回执收敛。`partial` 保存脱敏分段 Ref，相同part_id重送幂等；先保存部分输出，确认exit后再提交原结果，不能把不可变旧partial结果改写成另一个结果。

`timed_out` 只记录期限，不声称进程已停。`recover` 检查真实句柄，可能返回 running、stopped_outcome_unknown、identity_mismatch、unknown 或已确认终态；原动作ID始终阻止再次执行。原 `finish_action` 的终态可协调读取，标为 `original_host_action_ledger`（宿主回执），与本适配器的进程检查分开。

`scene.cancel(id)` 是可信宿主显式动作。Windows核对创建时间并持有句柄，调用系统taskkill /PID /T /F；POSIX只操作声明的专属进程组。权限不足/控制期限/不支持查询的宿主session保留cancel_requested与未知，不虚构取消。用户取消不会抹掉已有输出和来源，也不会终止其他动作。当前写入/控制仍受原scope、lease与原宿主owner约束，不自动接管另一宿主的控制权。

Windows本机已验证父子树取消、无关进程保留和身份不符拒绝；POSIX分支未在本机运行，不声称跨平台全面验证。

## 查询与 STATE

只读 `context.workscene` 支持 query/category/topic 及 offset/limit/budget，按实际UTF-8 JSON字节返回next_offset；没有取消/写入参数。query按解码后的字段检索，中文和Windows路径不会因为JSON转义漏匹配。存储状态不是即时活性；即时检查由原宿主 `recover` 执行。原brief/explain给出补读入口。

CLI复用原项目配置与会话：`contextrail workscene --operation read --session <reader> --input request.json`；写入、恢复和取消需原宿主 `--session <owner> --epoch <epoch>`。operation包括declare/complete/reserve/started/partial/timed-out/recover/cancel/finished/export，input是有界JSON；此CLI不执行argv。

`scene.export_state(new_workspace_directory)` 或CLI export输出STATE.md及scene.json，分来源声明、inferred、任务状态和运行/未知作业，带原Ref与宿主句柄。拒绝覆盖人工STATE，不改原不可变Bridge bundle或requirements账本；把此导出作为下一检查点的STATE材料，恢复时核对当前R状态。

导出的STATE使用原context_state_projection frontmatter并绑定真实R快照；工作现场JSON与候选schema接入原learning-origin v3过滤，避免把自己的恢复材料再次当新经验学习。显式新用户回复仍由原投影规则单独保留。操作日志在任务状态中只保留一个有来源的working_scene导航，其余按只读入口回读；大元数据截短带projection_loss，原来源不丢。

实现/验证与当前边界见 openspec/changes/recoverable-workscene/verification.md。原生Hook命令和信任设置未改变；现有MCP连接正常重连/新会话后可发现新增只读工具。
