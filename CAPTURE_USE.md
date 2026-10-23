# 采集脱敏与原文回读

F04复用ai-memory的MIT脱敏实现，固定来源、修改点和许可证见[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。两个上游研究副本在`.context-rail/upstream/`；运行时使用项目中的适配源码。

M的listener候选及游标、显式文档入队、事实retain、旧job继续处理及模型结果保存使用同一版本策略。JSON和嵌套敏感字段按结构处理，已知CLI参数、token、认证头、URL凭据和PEM用文本规则补充。新payload的`_capture`记录策略、角色及处理状态；外部输入自报`sanitized=true`不能跳过处理。retain在脱敏来源中校验quote。

这是已知形态处理：任意不带标识的随机字符串、自定义编码及全部混淆形式不在保证范围。默认无allowlist；不追溯擦除客户端日志、旧备份或全部历史队列。旧job仅在实际继续处理时经过新边界。

可信宿主已经显式保存的Rail原文不被改写。需要脱敏投影时运行：

```powershell
python -m contextrail redact-evidence --workspace D:/ContextRail --session host-01a161c7 --epoch 0 --source-ref '{"name":"已保存原文名称","revision":1}' --role tool
```

输出`processed_ref`和`manifest_ref`，可由现有`context.get`回读。manifest保留准确的source_ref、策略和角色。每个ref的byte range属于自己的UTF-8 revision；原文与派生偏移不能互换。源必须是UTF-8、最多4MiB，处理深度/项数有界。失败不会发布半份body/manifest；遵守现有owner、epoch和冻结状态。没有新增模型写工具。

本机M增量源码位于[integrations/engineering_memory](integrations/engineering_memory)，保持原listener和main所有权。deploy.py将canonical sanitizer和上游规则合成独立capture_sanitizer.py，后台无需额外安装ContextRail。用原control.ps1 stop并等待原任务停止，再执行`python integrations/engineering_memory/deploy.py`，由原control.ps1 start激活。备份在既有backups/capture-v1-*，deployment.json记录修改前文件是否存在。恢复时只恢复这份清单的受影响文件。

2026-10-23已完成9项本批行为用例、wheel构建和严格OpenSpec校验；本机原服务已备份/更新/重启，经既有共享服务retain实际写入main验证伪凭据已处理，未请求模型。[验收证据](openspec/changes/redacted-capture-boundary/verification.md)。
