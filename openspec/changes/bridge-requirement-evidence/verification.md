# A03 / D02 / A07 实现验收（2026-10-22）

## 范围与交付

完成 Bridge→Rail 单向导出、固定要求与终端导航。入口：bridge-import、bridge-packet、bridge-receipt，以及 status/checkpoints/search/continue/explain。使用说明为根目录 BRIDGE_USE.md。TODO 新勾选 A03、D02、A07，累计 11/72；持续实现整体目标仍 active，后续推进 A06 等依赖。

## RED 与 GREEN

新增 tests/test_bridge_requirement_evidence.py。原缺 bridge-import 的合法调用返回 2，而消费者期望 0，取得首个 RED。A07 的 status 同样先因入口缺失取得 RED。独立宿主约束用例两次发现丢失：首次 Bridge 导入丢掉初始宿主约束；随后修订漏掉导入后新增的宿主约束。现从前一 Bridge 派生集合与当前 task 区分来源，替换旧 Bridge 部分，保留宿主当前要求，达到 GREEN。

最终 `.venv/Scripts/python.exe -m unittest tests.test_bridge_requirement_evidence`：11 项通过，18.611 秒，无跳过。调用的是本机真实安装的 context-bridge/scripts/requirements.py，非模拟 validator。

| 对应要求 | 真实行为证据 |
|---|---|
| A03 原文/身份 | 原始账本 bytes、CRLF 来源、每个完整 row 均保留；ID/revision/status/scope/quote/superseded_by/acceptance 可按 Ref 恢复；bundle 不被回写 |
| A03 修订与幂等 | 完整重送返回同一 Rail snapshot、不增版本；同 checkpoint 改内容拒绝；新 checkpoint 改内容不升 revision 被原 validator 拒绝；R002 升 revision 后新 packet 生效，旧 Ref 可读 |
| A03 来源边界 | 错 task、缺 quote、越界来源均拒绝且 task version 不变；源单独被编辑或删除后，旧 packet 明确 stale_snapshot，存储原文仍可读，恢复原来源后可编译 |
| A03 原子性 | 真实 SQLite trigger 在第二件 artifact 插入时使事务失败；目标/约束/版本保持原状，证据与快照无部分发布 |
| A03 回执 | 原 Bridge receipt 校验器核对全部 ID/revision；有效声明返回 receiver_attested_not_machine_proven，旧 revision 或缺项拒绝，不自动生成 aligned 或取得所有权 |
| D02 固定要求 | 产品旧目标 superseded，自用目标为当前 objective；R002/R003 必需，延期和 superseded 不进入必需工作集；100-byte 预算报告具体固定 ID；初始及后来新增的宿主限制均保留，旧 Bridge 约束不残留 |
| A07 精确终端入口 | status 返回 task 状态；分页按提交顺序返回新旧精确 ID 与 store_current；实际执行返回的 resume.argv 恢复同一 Bridge/Rail 快照；explain 对应同一输入；旧游标拒绝，缺 snapshot 不自动选最新 |
| A07 任务隔离 | 同数据库另一 task 的秘密与快照不出现在当前列表/搜索；foreign snapshot 返回 not_found，超大页数拒绝，查询不改变 owner/epoch/version |

## 构建与规范

`openspec.cmd validate bridge-requirement-evidence --strict` 和 `git diff --check` 均通过。`uv build --wheel --offline --no-build-isolation --out-dir dist --python C:/Users/wblxr/anaconda3/python.exe` 成功生成包含 bridge.py/navigation.py 的 wheel；既有 bdist_conda 入口提示为非致命构建警告。

## 边界

本批只运行当前新增消费者用例，没有运行其他批次或原有回归套件。不把 11 项结果说成整个项目已回归。MCP/Hook 双端注册与当前客户端调用尚未实施，A06 保持未勾选；只读取了既有 hosts 指南和记忆 Hook 实现以定位下一接线点。未修改个人客户端配置、安装中的 Bridge 或记忆服务，也未新建监听器。

来源 byte 比较是运行时新鲜度与原文恢复契约，不是开发哈希探针。receipt 仅证明逐项覆盖与接收者声明。status/checkpoints/search 的 source_current=not_checked 不冒充当前源验证；实际 continue/bridge-packet 才核对来源。后续工具写入使 task version 前进后，旧 snapshot 明确拒绝；逐轮宿主刷新在后续接线处理。
