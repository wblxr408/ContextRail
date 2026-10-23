# Design

## Context

详见proposal。Compiler默认按完整条目装配，Bridge brief按scope选要求，E08已有原文时间线，原M队列导入有逐项结果；本阶段复用这些权威。用户要求直接适配上游，并按大阶段一次验收。

## Goals / Non-Goals

新增显式Compiler.brief、context.brief和CLI brief/explain路径；原compile和continue接口仍可用。Bridge.packet可显式layered，原生Hook保留原编译方式但增加loss和实际加载ref回执。STATE仅显式写入新文件，不修改不可变旧bundle。Python/SQLite/main与原监听器保持；不引入新摘要模型。

## Decisions

1. 直接移植ai-memory 1bd8663 excerpt.rs、profile.rs元数据标签和brief_tiers.rs覆盖/分层/短正文/低成本正文优先语义，保留MIT和差异。摘录是确定性预览而非事实摘要；Rail仅为text/Markdown/JSON生成正文层，JSON abstract/summary可用，Markdown frontmatter跳过且不引入YAML依赖。
2. 重叠SelectionGroup先合并为连通分量；必需/请求强制整个分量L2，其他分量按目录→L0→L1→L2装配。每次候选按完整JSON实际计量，包含loss/解释；不得按独立字符串成本相加猜预算。目录有offset/limit和context.list/brief补读，总数可见。无文本/超过展开上限的可选项留目录并说明原因。
3. loss独立schema，source/unit/scope固定，selected=完整内容单位，omitted=total-selected，truncated/unsupported是omitted的重叠子集，unknown表示范围外未观测。native memory只能报告实际已观察字符/bytes，独立Hook正文未知；不把prepared当consumed。目录导入是输入文件而非job数；重送0job不能推断0覆盖。
4. brief的同一结果就是本次解释；Bridge源在同一读事务核对，精确snapshot不自动选择。动作/时间线是独立有界页，有as-of/继续入口。最近原生准备回执展示当时信息，不重新编译冒充历史加载结果。
5. Bridge wrapper增加来源要求coverage；STATE以新文件独占创建导出含loss/ref/要求的JSON围栏，供当前handoff STATE引用。旧bundle不反写，省略只影响可选展示。
6. 简报和STATE属于派生历史，复用原learning_origin识别新schema/来源头，原导入只保留来源与history_only回执；不添加观察者。原M仅备份部署learning_origin.py一个文件，再由原control入口启动。

## Risks / Trade-offs

目录本身也可能超预算→保留计数和继续页，装不下必需/固定元数据则明确失败。依赖闭包跨页→跨页成员仅随整组L2加载，低层只展示本页入口。摘录不保证覆盖所有字面细节→标preview并保留L2/ref；二进制不强解码。原Store.load_snapshot会校验所有声明证据，本阶段不声称绕过其全量读取成本。

## Migration Plan

冻结整阶段消费者、统一RED基线后集中实现，再一次执行当前阶段验收与build/schema。具体失败才修复复验。无需DB迁移；新reader使用源码，原M仅在导入报告需要时部署最小overlay。实际用当前task生成简报/解释/新STATE，再勾选D01/D05/H02，整体goal保持active。
