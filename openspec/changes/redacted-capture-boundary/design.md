# Design

## Context

现有M app是安装目录，不是Git源仓库。listener.clean只平面regex，history_import.enqueue先clean再分片，Service在缩略前使用jobs内容；MemoryRuntime.retain校验quote在sources后持久化。Rail OutputRecorder属于可信宿主精确原文保存，不可直接改写。C06前置链为E08→E01→F01/F04，本批先完成F04，不提前勾选统一查询或事件时间线。

## Goals / Non-Goals

统一采集处理与独立Rail派生证据。既有精确Store数据、历史备份/原客户端日志不做追溯抹除，未知编码/混淆凭据不作万能识别承诺。F01/F02/E01仍有各自身份与归一化工作。

## Decisions

0. 按用户本轮纠正，直接下载两个上游到 .context-rail/upstream。固定 ai-memory 1bd866375b01031d63db54862a133376598d3701 / context-mode 90af14e34bf35120c3310f7a75f54fa99d2cabba。ai-memory MIT 的28条规则与shadow span方法移植到既有Python，不引入Rust运行时或第二套服务。env规则作JSON/重试适配；context-mode本批只读参考，未复制ELv2代码。来源及许可见THIRD_PARTY_NOTICES.md。
1. contextrail/sanitization.py为纯stdlib可审阅源码，可部署为M capture_sanitizer.py；M修改过的入口源码/补丁保存在项目integrations/engineering_memory，复用安装app其余模块，不复制整个后台或vendor。部署前一致备份，激活通过既有服务机制，未激活时明确缺口。
2. sanitize_capture生成不可变ProcessedCapture（规范JSON串、策略版本、角色）。外部字典的sanitized标记无效；重复处理保持稳定。限制深度/项数/文本体量，异常只给固定原因。clean包装先完整脱敏再裁剪。
3. 两步处理：从已知敏感字段/形态收集凭据，再处理所有文本，避免claims.value/quote重复裸值泄漏；短值只做完整值替换以减少误伤，直接敏感字段始终整个替换。字典保留非敏感角色结构，CLI列表处理已知凭据开关。已处理占位符幂等。
4. M listener入队对象、显式导入分片/元数据及retain claims/sources在持久化前统一处理；quote在处理后的来源中校验，处理标记写入payload。现有旧job进入模型缩略前再处理，不伪造旧记录已全量迁移。源身份如包含可识别凭据则固定错误拒绝，避免改写身份影响F01。
5. Rail redact-evidence只为可信CLI，输入明确source_ref/role，另存脱敏body和manifest，映射原ref/策略/角色/处理版ref，偏移属于派生revision。写入沿用owner/epoch/freeze与原子put_many，不给模型新增写工具。

## Risks / Trade-offs

规则漏识别未知编码/自定义secret → 声明已知形态边界，保留显式敏感键整值处理；不以通过测试宣称无秘密。规则误伤正文 → 策略有版本、原始任务证据独立、来源/角色可审阅。M服务持有旧模块 → 部署与运行激活分别验证，不仅复制文件就宣布完成。历史队列内容/备份不是追溯擦除范围。

## Migration Plan

先当前行为RED/GREEN，隔离临时队列/源目录调用真实M入口；备份现有app选中文件后部署增量，保留原模型/配置与main服务所有权。确认正在运行的工作状态再用既有服务机制激活，失败保留回执和可恢复备份。F04未全部满足前不勾TODO，不用沙箱夹具宣称真实运行已更新。
