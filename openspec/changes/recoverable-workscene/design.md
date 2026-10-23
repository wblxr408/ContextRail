# Design

## Context

原begin_action只保留pending/terminal，add_task_state保留来源和inferred。M normalized_events拥有精确client/session/call ID，捕获策略已在派生存储前执行。context-mode固定90af14e34bf35120c3310f7a75f54fa99d2cabba的session/extract.ts提供文件/任务/Git类别，但其调用级Git提取不能作为本项目操作成功证据。

## Decisions

1. 纯提取器适配直接文件字段、已结构化任务工具和有限显式中文标记。M只在已脱敏事件元数据加候选；R在原导入/宿主结果链把来源冻结到Ref后写原task_state，旁表仅管理来源幂等和topic/handle绑定。源调用和结果严格按workspace/client/native_session/call ID关联，跨页、先结果后调用可重放恢复；未知原文不猜脚本副作用。
2. 文件操作请求、实际观察、失败分开；Git仅解析独立argv/简单命令，成功退出也只有“命令成功”，commit需来源stdout实际回执才提取source-reported提交ID。cwd/argv解释器、明确环境字段和环境操作结果保留出处；不读取任意提到路径或保存整份环境变量。
3. TaskCreate/Update/TodoWrite/update_plan的completed只是completion_claim；显式用户放弃/阻塞保留事实声明，助手提取标inferred。宿主declare(topic,expected_call_id)在结果之前绑定，complete仅接纳同call的非partial、非timeout、exit0结果；错误结果/其他call/积极文字不关闭阻塞。推断状态不冒充验证完成。
4. WorkScene的动作方法是既有执行器的接线，不执行或重跑命令。reserve前台/后台显式选择，started登记已存在宿主handle。OS adapter用PID+creation time核对身份，恢复结果为running/stopped_outcome_unknown/identity_mismatch/unknown；只有实际exit回执或取消确认改变原actions终态。部分输出逐次持久化并脱敏。取消只作用精确所属动作，Windows持有进程句柄核对创建时间后调用系统taskkill /PID /T /F，POSIX仅专属进程组；未知/拒绝控制的宿主保留cancel_requested，原finish_action回执明确标为host attested。不存在默认新后台执行器或轮询。
5. stage读工具分页计实际UTF-8 JSON bytes，不提供写/取消参数；brief给计数和补读入口。显式导出新目录的STATE.md/scene.json，分verified/inferred/unknown和running jobs，拒绝覆盖人工STATE；不修改不可变原Bridge bundle或requirements所有权。

## Evidence and limits

Windows进程创建时间使用[GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes)的100ns FILETIME身份；[taskkill /t](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/taskkill)处理根与子进程。进程退出后若没有业务回执仍未知，不从PID不存在推断成功。OS控制权限不足、宿主session不可查询、进程已退出导致原树无法确定时如实记录未知，不以新PID重试终止。实际过程树取消和未关联进程保留须由本机消费者验证。

## Validation

冻结整阶段消费者后集中RED，覆盖原结果/原生事件、请求无结果、跨session、重复/重开、任务误完成、真实在途进程与树取消、未知/超时、隔离和STATE/分页；实现后整阶段GREEN，仅具体失败定点修复。按当前模块检查，不运行旧回归或探针。实际本机部署/指南回读后独立上传。
