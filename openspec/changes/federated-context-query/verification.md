# C06 / C08 / H01 阶段验收

2026-10-23，一次交付三源查询、按版本重用和本地状态。用户要求集中完成较大阶段后统一验收，未逐TODO执行检查。整体持续实现goal保持active，本阶段完成后累计29/72。

## 当前阶段消费者

冻结tests/test_query_stage.py的整阶段契约后，基线6项均在缺少公开context.query/context.status处RED。集中实现后一次执行当前阶段10项：9项通过，状态项在Windows临时库清理时因SQLite连接未关闭失败；修复local_status使用contextlib.closing后，仅重跑该失败状态项，1项通过（1.160秒）。没有宣称修复后又跑了一遍全部10项。

覆盖实际三源与原文ref、重复批次合并、缓存失效/刷新、来源故障独立、日期精度、完整JSON预算、路径/会话边界、原RPC传输与真实stdio消费者；状态区分捕获/学习/准备回执/出站控制。小预算未展示证据不记为已展示，来源不可用时不使用旧成功缓存冒充新鲜结果。

构建wheel和严格OpenSpec有效性检查通过。未执行旧批次套件、全项目回归、上游测试或smoke/health/import/startup/hash探针。

## 已应用到本机

原EngineeringMemoryListener通过既有app/control.ps1停止并等待退出后，备份R/M数据库和16个受管app模块，部署后由同一原入口启动。具体文件与备份在.context-rail/query-stage-deployment-receipt.json；只增加内部runtime的query_revision，未增加公开MCP服务或后台。

可信绑定为.context-rail/clients/self-use-01a161c7/v1/query-sources.json：精确workspace/task、原memory_home、当前Codex原生会话、text和20秒期限。四份既有受管MCP配置仅添加该绑定参数，其余原配置保留；备份在client-config-backups/query-stage-20261023T112906580634Z。Hook与原生信任未改。

## 一次实际业务链路

使用既有Codex reader配置启动新的官方MCP stdio消费者，脚本为.context-rail/scripts/verify_query_stage_live.py，完成回执为.context-rail/query-stage-live-receipt.json（complete=true）：

- 批量ContextRail/源事件/ContextRail，三源均返回，重复项coalesced_with=0；同一reader后续查询按未变版本重用。
- task精确ref经context.get回读，原生事件经原M get_event回读，main返回的项目偏好ref经Basic Memory read_note回读。
- 将真实QUERY_USE.md保存为implementation/federated-query-stage/QUERY_USE.md revision1，task version74→75；同一reader观察task缓存失效且候选总数增加。
- 经原Basic Memory edit_note更新用户实际的阶段验收约定并read_note确认；同一reader观察main目录版本改变、memory缓存失效。
- timeline视图和统一status返回，所选会话已捕获363项，workspace jobs为filtered36/queued147/skipped1，saved0；此数是验收时快照，后续监听器正常增长。
- status核对Bridge源为当前，history_control=not_controlled，未将已捕获/排队/准备注入声称为学习完成或模型已消费。

当前Bridge snapshot=a9c95b303ce84291945760e4c852c595；固定base=a558284943e241eab297739aca0864f2。active-native-client.json和query binding已记录该实际刷新快照。旧revision1本地bundle仍是创建时材料，不替代当前TODO。

## 观察到的限制

初次真实调用memory超时，task/events成功且错误显式；原服务日志同期记录冷启动超过20秒，本阶段没有隐藏该失败。服务就绪后真实三源链路完成。text不保证原worker无加载成本，后续空闲回收后仍可能先得到memory unavailable，需要再次查询。

长运行的旧MCP进程不会热加载新工具；重连同一既有配置后发现context.query/context.status。本阶段通过的是新stdio消费者，未冒称当前桌面旧连接已刷新。main笔记旧revision无法精确回读时明确unavailable，时间范围对task/main仍是候选页过滤；无跨源单一快照保证。

上游复用：context-mode 90af14e的src/search/unified.ts合并结构移植于_vendor/context_mode_unified.py，保留ELv2/MODIFIED，三源均参与两视图、未知时间不补造，来源内排名融合。详见THIRD_PARTY_NOTICES.md。无整套上游引擎/扫描器安装。
