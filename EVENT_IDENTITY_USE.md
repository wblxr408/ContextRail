# 来源事件身份与冲突

F01将采集身份与正文分开。原生completed-turn使用命名空间、workspace、原生session及Codex turn ID或Claude message ID；Claude ID为空时回退到uuid。缺少原生字段时使用完整来源路径、打开文件的代际及行起点，标记`source_position`。这种弱身份不保证文件搬迁/原地重写后的全局去重，完整轮换由F08继续实现。

新job的`_event`包含稳定id及coordinates，可由后续Rail事件桥引用。正文不进入该id，传输offset、归档路径、接收时间及处理标记也不改变原生事件身份。不同来源事件即使正文相同也分别保留。同一事件同内容重放沿用一个job；正文不同保留首次job并报告冲突，不自动生成新知识。

来源认领、队列和游标在既有M state.sqlite中同事务提交。采用ai-memory MIT的keyed insert方法，不使用其来源键TTL，也没有引入独立后台。旧job保留原id、状态与已有事实引用，重放时延迟建立别名。原数据有多个可能job则显示`ambiguous`与全部可用别名，不自动选择。没有全量改写已保存事实或自动撤回旧重复学习。

显式文档/Git先绑定完整处理后修订，再给分片生成身份。同修订增加、减少或修改内容都会拒绝整组，原job集合保持；冲突审计在拒绝后仍可查询。应以新源修订导入实际更新，不能重用旧revision掩盖变化。

本地可信只读诊断使用已安装入口：

```powershell
& 'C:/Users/wblxr/AppData/Roaming/uv/tools/basic-memory/Scripts/python.exe' 'C:/Users/wblxr/.codex/services/engineering-memory/app/listener.py' events --limit 20
# 精确选择上一步返回的 _event.id
& 'C:/Users/wblxr/AppData/Roaming/uv/tools/basic-memory/Scripts/python.exe' 'C:/Users/wblxr/.codex/services/engineering-memory/app/listener.py' events --event-id source-event-v1:替换为返回的ID
```

返回metadata、旧job别名、冲突原因/字段及有界分页；不回显正文。每项最多100个别名和10个冲突，附总数及truncated标记；`recent_conflicts`还包含在整组回滚后保留的导入冲突。此入口不是全量任务经历时间线，也没有给模型新增写接口。

部署沿用[integrations/engineering_memory](integrations/engineering_memory)和既有control.ps1。canonical源码在[contextrail/source_events.py](contextrail/source_events.py)，部署为独立event_identity.py；MCP/main和学习预算保持。回退时恢复受影响app源码，保留新表及原job，不做删除表的破坏性降级。

本批导入用例还发现Python re在长连续文本上与上游Rust正则性能不同。已为URL scheme增加起始边界，为auth header前缀加长度界限，策略升级为`rail-capture-v2-ai-memory-1bd8663`。这个适配不宣称与Rust实现完全等价，来源差异见[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
