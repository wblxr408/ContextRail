# E02 / E03 / B08 阶段证据

## 当前模块消费者

tests/test_workscene_stage.py 初始17个消费者集中RED（0.623s）：原接收器未生成工作现场，其他用例在原只读入口缺失处失败；没有把缺Python模块的import错误算RED，也未在基线启动进程夹具。

实现后整阶段17项运行4.416s，6个目标通过、11个目标失败；报告的15个error包含原生夹具关闭错误的重复cleanup报告。已通过目标包含真实父子进程树取消、无关进程保留、身份不符拒绝、超时仍运行和有界只读分页。失败分别修复原结果Ref实际名为result、无可查询PID的宿主句柄状态、重开后reader绑定、JSON转义比较与M调用方事务/连接关闭/Windows workspace_key夹具；再修复scene_inputs占位符数。仅11个失败目标、其后4个原生失败目标定点运行；原生4项目标最终1.206s通过。跨session/捕获排除两个用例在夹具真正进入所选session后再定点通过0.659s，避免空数据造成假GREEN。

实际接线缺口补充6个当前目标：原finish_action终态协调（RED→GREEN0.129s）；进程结束且宿主释放句柄后结果未知（首次目标GREEN，API调整后同进程目标组GREEN2.234s）；按解码文件字段直接查询（RED→GREEN0.130s）；宿主实际环境声明（RED→GREEN0.227s）；约112KB原生命令不因派生元数据太大而停在session_meta页，以及操作日志不累计成必需上下文（两项RED→GREEN4.061s，3次观察从12个active状态变为1个工作现场导航）。

收尾发现新工作现场schema未接入原自动学习过滤，第24个当前消费者观察到自身派生视图eligible=true；加入learning-origin v3及原STATE frontmatter协议后定点GREEN0.158s。累计24个不同消费者有对应GREEN记录；不是最终同时重跑24项。新边界修复后只重跑相应目标，未执行旧回归/全量suite、smoke、hash、文件存在/import/startup/浅健康或措辞匹配测试。Windows真实树取消检查实际子进程不再运行，未以取消请求当确认；PID复用拒绝用故障注入模拟不同创建代，并未声称真实复用全部可能性。POSIX适配未在本机运行。

## 部署到原 M

通过原app/control.ps1 stop确认原PID141852/141988退出后，备份原state.sqlite和本阶段4文件至C:/Users/wblxr/.codex/services/engineering-memory/backups/workscene-20261023T152254970378Z。仅增加context_mode_scene.py、workscene_extract.py并更新native_event_stream.py和notice，原模型/预算/任务/源订阅不变；原start返回0。

一次前台受控写入遇到后台写锁，未计成功。停止原lifecycle后使用原Store同一库捕获受控文件；本机脚本最初误用了ingest参数顺序，修正为原API ingest(path,kind)、list_events(db,...)后完成。处理期间没有私有服务或新默认观察者，最后沿用原start恢复。

原M真实部署代码处理受控Codex格式3事件，tool.call元数据包含3个scene候选（command/git/environment）；输入明确为受控格式，不是原生用户UI历史。工作目录.context-rail/live-workscene/20261023T152842534237Z，随后在独立controlled-native示例R scope导入3事件并形成6个有出处事实，未把合成用户要求写进真实任务。回执workscene-memory-live.json。

learning-origin v3最后单文件部署备份为backups/workscene-learning-20261023T153728350579Z，原stop/等待/start完成。安装目录版实际读取原任务STATE.md和scene.json，分别标context_state_projection/workscene_projection且eligible_for_learning=false；回执workscene-learning-live.json。没有添加第二个记忆监听器。

## 原任务真实工作现场

使用原scope与host-01a161c7 epoch0，在原R库登记workscene-stage-git-status显式foreground动作，宿主实际执行git status --short、保存实际退出0/stdout/stderr和宿主解释器/prefix等环境声明。先绑定inspect-working-tree，再以该结果确认此读取步骤完成，不把它声称整个目标已完成。原OutputRecorder自动投影并同步原actions为succeeded，部分输出Ref与STATE均保留。

第一次收尾因M回执尚未就绪而中断，但R动作和事实已提交。权威回读确认job已succeeded后重试收尾，reserve返回不再执行；版本136→136且保留同一结果。最初原任务121→136，未重新运行Git来伪装恢复。实际R备份含before-workscene-20261023T152942717196Z.sqlite3，原始首次备份也保留。

收尾增加学习过滤说明后，真实任务当前snapshot18a37aa7cafb437fbab2ba54e17d5c7c、task/fact137，base9312c1032756423e9aac7dfcb3c9a395，schema10。指南implementation/recoverable-workscene/WORKSCENE_USE.md revision2，官方MCP精确回读6060bytes；context.workscene按git/status返回1条真实观察、800bytes，Bridge source_current=true。原任务STATE导出到.context-rail/bridge-workscene/self-use-01a161c7，源事实/任务/作业共11条；CLI同源查询返回801bytes并实际导出STATE成功。active/query binding已更新；Hook命令/trust未改。完整回执workscene-live-receipt.json complete=true，保留prior_runs；原snapshot4fc5d17375a04cafb4e72fea63cb15a8/version136/guide rev1为历史。新一轮仅guide改动使136→137，reserve_execute_once=false、受控native重放新增0事件，没有再次执行Git。

## 边界与交付

所有来源为显式导入或宿主提交，M只给候选；不自动导入全历史，不把只读查询变为状态写入。请求不证明结果，Git回执不证明实时HEAD，环境字段是宿主声明而非argv推测，未知不自动重跑。元数据可截短并带projection_loss，完整source Ref保留；操作日志通过单一active working_scene导航回读，强制要求/任务不被文件日志挤占。控制操作受原scope/lease/host owner约束，未知/不支持控制的原宿主session需由其宿主继续处理，不接管另一宿主控制权。STATE导出拒绝覆盖人工文件，也不改原不可变Bridge要求。

最终wheel构建与严格OpenSpec校验通过，日志.context-rail/workscene-final-build.log；格式检查和模块独立上传在收尾补记。wheel不发布，保留用户既有evaluation/research修改及其他untracked。
