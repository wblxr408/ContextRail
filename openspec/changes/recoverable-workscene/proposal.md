# Proposal

## Why

原生事件已有调用和结果，R已有task_state/actions，但恢复时仍需手填工作现场、任务变化及在途句柄。仅有命令请求不能证明Git成功，进程消失也不能证明业务完成。

## What Changes

- E02：原M事件投影、R事件导入和宿主结果接线，保留文件/范围、cwd、命令、Git结果及环境来源，区分请求与观察。
- E03：任务、计划、阻塞、放弃和完成声明形成带来源的结构状态，完成需预先绑定的实际结果，积极措辞不解除阻塞。
- B08：原actions登记显式前台/后台选择、宿主session和进程身份、部分输出、超时/取消/未知终态；可恢复查询及明确取消树，无自动重跑。
- 有界只读工作现场入口和Bridge STATE导出，原快照/简报携带补读入口。

## Capabilities

### New Capabilities
- `workscene-recovery`: 来源绑定的工作现场、任务转换和在途动作恢复。

### Modified Capabilities
无；复用既有状态/动作/事件协议，未归档主spec inventory为空。

## Impact

Python/SQLite既有栈，M event_stream、R OutputRecorder/event_bridge/store/CLI/read tools/brief及B STATE导出。宿主继续实际执行，不建立新命令执行daemon/默认观察者，不向模型开放取消或状态写入。
