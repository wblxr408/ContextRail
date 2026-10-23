# Verification

2026-10-23，Windows；本批用例使用Anaconda Python，本机共享服务使用原basic-memory uv环境。

## 当前行为

- 原history_import.enqueue把嵌套JSON密码/API key写入真实临时jobs表，“凭据不得持久化”断言失败；适配后通过。
- Rail新CLI先因命令不支持而失败，接入后原文/处理版/manifest回读通过；UTF-8后续文字的偏移不同，分别正确定位。
- 旧ready_to_save job的result留有伪凭据，断言失败；补充处理后result持久化再通过。
- `python -m unittest tests.test_capture_redaction`：9项当前行为通过，覆盖文档/原生队列及游标、retain权威Markdown与重复提交、旧job保存、伪造标记/角色/嵌套CLI、零宽token/PEM/URL及中文、固定错误，以及二进制/错误租约/冻结时无派生写入。
- 未执行旧测试套件或回归。wheel构建通过，MIT LICENSE随wheel打包；现有conda可选entrypoint警告未阻止构建。`openspec validate redacted-capture-boundary --strict`通过。

## 本机接入

原control.ps1 stop使EngineeringMemoryListener停止。备份：`C:/Users/wblxr/.codex/services/engineering-memory/backups/capture-v1-20261023T023417271555Z`。6件增量文件替换后经原control.ps1 start激活。

通过既有共享服务engineering/runtime retain写入实际main，event_id=`contextrail-f04-20261023-capture-v1`。源文件`engineering/memory-facts/308a41d25dfc58efaaeb459752533515.md`包含策略与占位符、原伪凭据不存在；未请求模型。完整回执`.context-rail/f04-live-receipt.json`。源事实明确技术边界，将伪凭据标注为验收样例。

## 边界

历史日志/备份没有全量重处理；不覆盖任意随机秘密或全部编码混淆。context-mode仅下载审阅，本批未复制ELv2源码。F01/F02/F03/E01/E08/C06不由本批完成。
