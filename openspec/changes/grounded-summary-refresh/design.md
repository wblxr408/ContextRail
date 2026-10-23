# Design

## Context

CompactionLifecycle 只有可选外部 validate；Store.complete_compaction 可绕过它。M reflect 只验证引用在允许集合及 quote 存在；page 原六列缓存覆盖当前文件，generation 是全库文件元信息。notes() 排除派生页以防自我学习。

## Goals / Non-Goals

实现 D03/D04/D06 的原调用链、历史和故障语义；不引入新数据库/服务/观察者或模型。有限规则能证明字面覆盖和声明来源，不能证明任意自然语言蕴含；明确报告这一范围。真实模型质量不由假生成器证明。

## Decisions

1. canonical fidelity 模块提供按来源 UTF-8 byte span 的 residue 与确定性报告。已存 R 正文和 M 已脱敏 vault 为输入；不去解析脚本或越权读取提到的路径。每来源扫描/条数/上下文有界，loss 说明扫描范围和遗漏；Windows/中文独立适配。参考 ai-memory 固定 1bd866375b01031d63db54862a133376598d3701 的 keep_tokens.rs 思路与少量 regex（MIT），明确适配许可证。
2. R gate 在原 Store.complete_compaction 事务边界执行，先持久保存尝试；原精确 span、scope/lease、current 校验仍控制权限。检查 snapshot constraints、acceptance、显式 resume-intent 最后限制，以及所选 span 中版本/数值单位/否定的覆盖与新增冲突。错误码、路径等可仅存 residue，不强迫摘要复述。回读尝试包含草稿、报告、原精确来源；搜索摘要可通过 residue 找到省略细节。
3. M reflect 在现有 citation/quote 验证之外输入完整所选原文的有界 residue，允许 source/inference/unknown 声明，source 声明需精确支持；输出的版本/数值必须可从其引用来源找到，并保留相关否定。有相关 observation 时须保留当前 user authority 的值。无 generator 仍是证据模式；覆盖规则报告不冒称语义证明。
4. M page 在原 SQLite 增加版本/尝试表，不改变原六列缓存兼容性。每次生成冻结 catalog generation 和引用证据版本，完成前复核；不可变版本文件和原路径当前投影先写后切 DB 指针。失败/崩溃前旧指针不变，孤立版本不当作当前；当前投影读取由 DB 版本修复。旧版与失败草稿可通过原 page 的 history/version 参数读取。stale 返回 content=null 和旧版本引用；刷新失败/来源变化保持 stale，禁止作为当前正文输出。旧六列缓存迁移为 legacy_unverified 历史并保守 stale，显式 refresh 建立新绑定。
5. 全库 generation 故意保守：新增可能冲突来源也使页面 stale，绑定集合用于具体出处；不启动新监听器。外部直接读取历史文件不由此拦截，tool/page 入口负责 current 语义；知识页路径 frontmatter 标明版本和来源。

## Risks / Trade-offs

确定性门可能拒绝合法改写，宿主可缩小精确 source spans 或明确标注推断/未知，不能静默跳过门。全库变更会造成较多 stale；未来可在足够来源关联证据下细分。文件和 SQLite 不能跨介质原子，采用版本先写、DB 当前指针权威和读取修复，保留 orphan 供调查。现有原始历史不追溯清洗；当前导出不得把旧内容重新声称当前。

## Validation

本阶段消费者冻结后一次基线 RED，再实现，整阶段 GREEN；涵盖中文/Windows byte spans、摘要省略错误码仍找回、最后限制遗漏、错版本/数值/否定/引用、拒绝回读、来源改写/删除/生成中变更、失败保旧/重开/历史/旧缓存。只运行当前阶段；原模型不可用时保留明确剩余实模质量边界。完成本机最小部署、指南回读、构建/严格校验后提交上传。
