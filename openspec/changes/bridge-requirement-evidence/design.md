# Design

## Context

Bridge requirements.py 已负责原文引用、revision、替代链、brief 与逐条回执。Rail 已有不可变 artifact、快照、租约和 Compiler；现有写 API 各自事务，跨步骤发布需要共同事务。

## Goals / Non-Goals

Goals：完成 A03/D02 的真实来源、单向导出、固定要求与过期拒绝。Non-Goals：更改 Bridge 权威、自动生成接收者自然语言对齐声明、安装个人客户端 Hook、新记忆后台。

## Decisions

1. 使用可信宿主指定的已安装 Bridge requirements.py 子进程，原校验器继续负责语义结构，避免复制一套 validator。先只读捕获本地 bundle 的 requirements.json 与声明的 source/evidence，原样写入临时隔离副本再校验，同一副本用于持久化；路径不得越出 bundle，总量/文件数有界。CLI 入口仅宿主可调用。
2. task_id 必须等于已绑定 Rail task。Bridge snapshot_id 必须明确且属于 task namespace。artifact 名使用稳定命名空间；Bridge revision 保留在正文/manifest，和 Rail artifact revision 分开。原文 bytes 不经文本模式改写。
3. 导出 manifest 保存全部行的引用、源文件引用与 snapshot identity。每个 Bridge snapshot/scope 的导出不可变，同身份不同内容拒绝；完全重送不改变任务版本。当前指针单向推进；后续修订复用原 validator --previous 检查。
4. Store.publish_context 将工件、派生目标/约束/当前验收、Rail 快照及发布索引事件同一事务提交。复用现有写逻辑，提取事务内 snapshot helper；不创建新表和迁移。失败回滚目标、工件、指针和快照。
5. goal/constraint/always_load 的有效行必需；其他选中 deliverable 可选。superseded 行保留历史而不进入必需工作集。首次导出保留独立宿主约束/验收作为 baseline；后续只替换 Bridge 派生部分，不能删除宿主硬约束。当前目标来自有效 Bridge goal，原运行时目标另存 baseline 以供追溯。引用与接受标准保持原值。Compiler 继续按完整 UTF-8 JSON 预算编译，失败明确列出固定 ID，不能降级硬要求。
6. 按精确 Rail snapshot 取得 export。编译/回执前核对当前导出指针与原始 bundle 的实际 bytes；源变化或缺失明确拒绝，不能用尚未重新导入的旧副本冒充当前权威。历史原文仍能按 Ref 读取。
7. receipt 在存储证据还原的临时 bundle 上调用原 Bridge receipt 校验，接收者实际填写每个 ID/revision/interpretation/next_action。结果仍只是 receiver attested，不提升为机器证明语义正确或接管写权。
8. 连续实现推进 A07：只读导航门面复用 scope/session、快照、原有搜索和 Bridge 编译。列表按提交顺序分页、标记 store_current 与源未复核状态；continue/explain 必须显式快照，不从最新记录猜。每条提供 JSON argv（及 PowerShell 安全字面量命令），固定当前配置的 workspace/store/project/task/session，不创建聊天或激活写权。

## Risks / Trade-offs

原校验器是可选本机组件，未安装时需要 --bridge-validator；不得声称 Bridge 接线已部署。每次编译核对声明来源有本机 I/O 成本，先以 64MiB/1024 文件/2MiB 账本限制边界。丢失本地源时只能明确读取历史，不能默认为当前。task 要求在 Bridge 专用派生 task 内更新，必须显式相同 task_id，不能借 bundle 重绑 scope。

## Migration Plan

无 schema 迁移；新增模块和 CLI 可独立停用，既有 Bridge bundle 与长期记忆不变。旧导出保留，任务新版本使旧 Rail 快照无法当当前编译。
