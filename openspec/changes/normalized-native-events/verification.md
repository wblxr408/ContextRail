# Verification

2026-10-23，Windows，E01本批验收；整体持续目标仍active。

## 行为与修复

初始真实临时队列消费者RED：用户记录没有final时来源表没有请求。接入原游标事务中的归一化/认领后，重开服务仍可回读完整处理后请求，且没有新增知识job。新增event-import消费者也先因命令未支持RED，再接通任务保存/回读。

旧库升级消费者RED复现：原cursor有session/workspace但没有_stream，新状态丢失session绑定。修复为继承原绑定、保守保持旧turn未知，不自动补录已越过的历史。结构化工具结果后续补充两个RED：媒体/thinking块被保留、JSON Schema的type数组被误当内容类型而丢事件。修复后保留公共文本/JSON结构，明确省略已知私有/媒体块。

`python -m unittest tests.test_native_event_stream`最终16项通过，8.450s。覆盖无final崩溃重开、源文件删除后独立回读、双端tool/result/final/lifecycle、中文/CRLF和跨UTF8字节范围、来源时间缺失/无时区/无效、脱敏/省略、重复与冲突、原子cursor回滚、旧库升级、workspace/client/session选择、Rail租约/冻结/第二artifact失败回滚及重复导入、真实官方SDK MCP回读工具结果。

wheel构建通过，MIT/ELv2许可均随各移植模块打包；现有conda可选entrypoint警告不阻止构建。严格OpenSpec及本批tracked diff检查通过。未执行旧批次/全项目回归、上游测试或startup/health/checksum探针。

## 本机来源与部署

先停止原EngineeringMemoryListener并等待原pid退出，备份原M库与Rail库：

- `C:/Users/wblxr/.codex/services/engineering-memory/backups/before-native-events-20261023T051428373644Z.sqlite`
- `D:/ContextRail/.context-rail/backups/before-e01-events-20261023T051428373644Z.sqlite3`

初次app备份`capture-v1-20261023T051428993462Z`。结构化块修复后再次停止/备份/部署：M一致备份`before-native-events-final-20261023T052233074297Z.sqlite`，app备份`capture-v1-20261023T052234055439Z`。每次10件增量，均用原control.ps1启动；没有注册新观察器/任务或增加默认记忆服务。

从实际Codex JSONL逐字选取6行：session元数据、用户原请求、相关turn_context及工具调用/结果。`.context-rail/e01-source-selection.json`记录原始与选段字节区间；选段`.context-rail/evidence/e01-native-selected.jsonl`没有完成回合，没有编造用户/工具结果。原会话为`01a161c7-38d2-7333-a025-9d0be8d6a62a`。

实际安装Store.ingest保存选段，源对应知识job=0。另观察原监听器自动写入真正原会话文件的事件：首次7项，最终部署后回读时22项。该数是功能数据，不以heartbeat或进程存在替代采集成功。

验收脚本首次在Rail导入前因原uv环境未包含项目路径而ModuleNotFoundError；补显式项目源码路径后重用同一冻结来源，未重建/改写源数据。这是执行环境修正，不作为TDD RED证据。

## 当前任务与原生MCP回读

实际task self-use-01a161c7、owner host-01a161c7/epoch0，导入3个事件各body/manifest共6件，fact version68→74；runtime刷新snapshot=`75891fb8b28d446999ef355f73ba1ef0`。固定Bridge base与原始要求不变。再导入全部existing，version74→74，没有重复revision。

| 事件 | stable ID | 原生context.get实际结果 |
|---|---|---|
| user.message | source-event-v1:a9cd3019a1bd55118ff39548ce59398d | 1412 bytes，包含用户“更多的实现…直接下载”原请求 |
| tool.call | source-event-v1:27a26a013b7e56a59ecdb8089615ef80 | 任务SDK精确回读，保留call关联 |
| tool.result | source-event-v1:402fdaa84fe45946baa7f37b17ade0f0 | 10193 bytes，4个input_text结构块，call_id=call_64f3d8bdc23d4b5f908439c1dfe46901 |

用户/工具结果已通过当前聊天真实mcp__contextrail context.get逐项读回，均标untrusted_evidence、来源时间provided。未以“输出是字符串”假设限制真实结构化结果，也未把证据当新指令。

首份回执`.context-rail/e01-first-live-receipt.json`保留最初imported状态；最终`.context-rail/e01-live-receipt.json`保留existing重放及22项自动原源事件；部署回执`e01-deployment-receipt.json`保留两阶段备份。前台未请求知识模型，后台仍沿用原预算。

## 边界

原日志历史未自动全量回填；实际正向源采集验证为Codex，Claude兼容由实际字段勘察与本批消费者用例覆盖，不声称新增付费Claude会话验收。4MiB处理界限及明确块省略仍适用，源JSONL路径只作出处；存储的处理版独立可恢复。F02/F03/F08/E08/C06仍分别推进，不将本批称为完整时间线或统一查询。
