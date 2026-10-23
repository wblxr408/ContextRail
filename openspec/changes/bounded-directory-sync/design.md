# Design

## Context

ProjectConfig已固定workspace/Scope与capture.ignore，Store._put及FTS支持原文/版本原子提交。现有M history_import.enqueue是前台文档分片与jobs幂等入口，安装app不是Git源仓库；本批在Rail写可审阅适配并直接复用原入口，不复制M后台。

## Goals / Non-Goals

显式目录同步与续读，内容修改/删除协调，及可选明确文档入原队列。不增加默认监听、网页抓取、全量代码知识化或即时M知识页更新；后者D06/E02仍保持各自范围。

## Decisions

1. schema8增加directory_sources/runs/files/entries：source按scope+实体workspace/root稳定命名；run随机ID、step CAS、工作队列和计数持久化。根与选择政策固定，预算可增大；新run替代同source旧run，不允许旧游标推进当前状态。
2. ProjectConfig.directory提供include/exclude/gitignore及预算，默认32文件、深度6、单文件1MiB，硬上限1000文件/64层/16MiB；每页stage最多16MiB。目录单层枚举默认20000项、可提高至200000；队列元数据最多8MiB，触顶partial并给缩小root入口。
3. 原生Git check-ignore --no-index -z --stdin处理gitignore。普通目录用临时Git元数据与work-tree解析已有规则，不初始化用户目录。Git不可用则respect_gitignore模式明确失败，不用不完整自制gitignore冒充。include/exclude是按路径段匹配的glob，支持**零层/多层。
4. 跳过symlink/reparse/special文件，严格resolve containment；有界打开后核对最终句柄路径/文件身份，再读与复核stat。文件不稳定、I/O错误、深度/体量限制保留待处理；扫描是逐页观察，不宣称原子文件系统快照。
5. I/O在写事务外stage，提交时复核owner/epoch/freeze、run step与源版本。每页工件、FTS、来源映射/条目/游标在同一事务提交。删除仅在队列全部成功消化后协调，partial绝不删除未见旧文件；旧ref明确historical/unavailable，不伪造旧快照current。
6. 实体文件路径不当作SQL/命令或工件路径执行，工件名用稳定source/file ID，目录映射保留原相对path。FTS标题补原路径，返回source_path；全局索引coverage包含未完成目录计数，结果内两流检索不受无关目录partial影响。
7. directory-memory-enqueue仅显式CLI，owner租约校验，读取run条目的冻结ref。只送原M支持的.md/.txt/.json，沿用utf8-sig/通用换行、clean/enqueue；event identity绑定scope/source/ref revision与首次版本stat时间，重试单一业务效果。写入已有state.sqlite队列，不新建默认库，不声称分类/知识页更新已完成。

## Risks / Trade-offs

文件系统可在两页间变化 → 记录观察版本，完整扫描后再协调删除，下一次sync发现后续变化。Gitignore规则逐页生效 → 明确observational而非原子快照；显式选择/项目ignore政策变动时旧run拒绝，重新开run。大目录触顶 → 保留partial/具体原因和resume或缩小root入口，不隐藏范围。

## Migration Plan

schema7→8只加元数据表，不改已有任务/工件/快照；旧程序拒绝新schema。实际项目操作前SQLite一致备份，使用新source namespace逐页导入；每页可重试，失败不推进step。M入队与Rail回执不是跨库事务，依赖原队列稳定ID重试；先记可审阅证据，不启动分类worker。
