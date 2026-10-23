# Verification

2026-10-23，Windows，Python既有栈；F01完成，持续目标仍active。

## 本批行为证据

开始时3项真实队列用例RED：同turn异内容产生2个job、无原生ID的不同位置同文只保留1个job、job缺稳定来源标识。实现source coordinates与事务认领后GREEN。

导入修订边界RED：同修订追加尾部分片使4个job增为6个；旧分片重复导入因处理标记不同被误判冲突。整份container绑定、语义比较及旧别名接线后GREEN。另补Claude message.id=null的实际行为RED（只留下1个job），修正uuid回退后保留2个。

长连续文本导入出现35秒TimeoutExpired，定位Python re对上游URL/auth header规则的回溯差异。增加scheme起始边界、限制header名称前缀，策略升为v2；相同导入消费者恢复完成。未宣称与Rust全部等价。

`python -m unittest tests.test_source_event_identity`最终本批16项通过，6.208s。覆盖Codex/Claude、fallback位置、原生归档重放、workspace/namespace/字段边界、旧saved job/事实引用保留、旧歧义、两个连接并发、写失败后认领与游标回滚、整组分片失败回滚、增减分片、旧分片兼容、只读CLI和真实Git修订重送。随后细化旧库fixture为无3个来源表的数据库，重新打开升级后该消费者单独通过0.359s。

wheel构建通过（既有conda可选入口警告非致命），严格OpenSpec及本批tracked diff检查通过。未跑旧批次/全项目回归、上游测试套件、启动/health探针或checksum检查。

## 本机数据与部署

原control.ps1停止计划任务，并确认原pid退出后，对原state.sqlite执行SQLite backup。一致备份：

`C:/Users/wblxr/.codex/services/engineering-memory/backups/before-source-events-20261023T030520170913Z.sqlite`

原app增量备份：`backups/capture-v1-20261023T030520947368Z`；7件增量部署后经原control.ps1启动。最后只读诊断的截断计数元数据更新，另备份`backups/event-identity-metadata-20261023T031046625224Z`，CLI每次调用读取当前模块。未增加后台、默认store或修改模型预算。

通过实际安装history_import入口导入冻结指南：`.context-rail/evidence/f01-current-implementation.md`，明确源修订mtime_ns=1792724805320734100。首次入队1、精确重送0；同修订追加测试正文被拒，原job集合保持。job=`source-event-v1:9676dcc63baf5c6c90bc0b6b5deb13b4`，container=`source-event-v1:7fa93be59ac8520694c1b97873eb135c`。

实际安装listener.py events按该container精确回读，显示content_changed和fields=[final]，不回显正文；别名/冲突总数与truncated标记可见。`.context-rail/f01-live-receipt.json`、`f01-deployment-receipt.json`保存本机回执。

既有共享runtime实际retain保存正则适配教训，源`main/engineering/memory-facts/f860f4b5d64a5737a3e6b8ec851e405a`带`rail-capture-v2-ai-memory-1bd8663`；native Basic Memory直接读回确认。前台未请求模型，资料队列仍遵守既有后台分类预算。该技术事实按既有来源规则保持experimental。

## 保留边界

旧已执行的重复知识不自动撤销；歧义列候选待处理。缺原生ID时定位身份不是跨搬迁保证；原地同长改写和完整日志轮换仍是F08。F02捕获owner、F03派生材料过滤、E01原始事件与E08时间线仍未完成。本批没有把新来源表称为完整事件流或统一查询。
