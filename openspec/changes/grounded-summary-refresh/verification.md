# D03 / D04 / D06 阶段证据

## 当前消费者

tests/test_grounded_derivation_stage.py 冻结当前12消费者，基线集中观察12项预期失败（3.869s）；第一次运行的子进程默认Windows编码产生5个基础设施错误，改用UTF-8后才计这次有效RED，未改产品以制造失败。

实现后整阶段12项运行4.258s：7项M消费者通过，5项R因新draft表11列/12占位符错误失败。修复占位符后只重跑这5项，0.312s全部通过。

随后按实际接线缺口补充5个当前边界消费者：Bridge必需active statement未被所引span覆盖时仍必须核对（RED→GREEN 0.101s）；归档文件写失败不切当前指针（首次目标运行通过）；RemoteRuntime原page方法不接受history/version（RED→GREEN 0.586s）；只读工具分页后实际JSON字节不超过1400且能读到E0427（RED→GREEN 0.108s）；新derivation attempt原始query凭据落盘（RED→GREEN 0.556s）。累计17个不同消费者有对应GREEN记录，未声称最终同一时刻重跑17项。

覆盖：中文/Windows路径、命令、URL和UTF-8原精确span；摘要省略错误码仍可检索/回读；最后限制遗漏、版本/单位数值改错、否定丢失拒绝；原Store直接提交不可绕过；宿主validator拒绝可读并可重试；M摘录外残余进入输入、source/inference/unknown；来源修正/删除/生成中变化；刷新拒绝保留旧版本和草稿；跨重开历史；旧六列缓存保守stale；归档失败、原远程适配与有界只读回读。

只运行本阶段目标，没有旧套件/回归、smoke、健康/import/startup、hash或实现镜像检查。实际模型语义质量不由有限规则或假生成器证明。

## 实际部署与原服务

原app/control.ps1 stop后确认已观察到的原lifecycle PID134040/134476不存在（原调用已自行结束），再备份原state.sqlite及本阶段8文件并部署：C:/Users/wblxr/.codex/services/engineering-memory/backups/grounded-summary-20261023T143550257588Z。原start返回0，首次资源端点尚未准备好没有被记作成功；随后原process107236/133924及业务调用完成。

原远程page首次宽泛查询达到请求期限，明确报告写入不自动重试。精确查询原SQLite确认同名current和attempt均为空后，改用引用限定查询，未改模型/预算或起私有服务。原RemoteRuntime创建“ContextRail 共享资源退避证据”：version1525406c5b7e4030b5367a1dfe958b0c，正文385 UTF-8 bytes，4个真实source bindings，历史1版、精确历史正文相等。既有Basic Memory原生MCP memory_knowledge_page再次返回同版本current、stale=false。回执为本机grounded-memory-live.json和grounded-native-mcp.json。此链路是原资源服务evidence-only页面，不发生synthesis模型调用；不概括整个后台阶段零调用。

查询凭据定点修复等待原在途classifier完成后，确认原PID107236/133924退出，再备份到backups/grounded-query-redaction-20261023T144543147291Z并只替换memory_runtime.py。45秒等待曾超时，不据此重启或强杀；后续原日志和进程句柄证实已停止，待评估工作仍在原队列。原start返回0，安装目录版同一凭据消费者定点通过0.434s。仍沿用原control和资源worker，长连接经原有调用链读取；没有重复Hook信任请求。

## 任务材料

指南GROUNDED_SUMMARY_USE.md revision2保存到原R任务，fact/task120→121；当前snapshot eab0113ca7734f4d91cfec5076d5722b，base9312c1032756423e9aac7dfcb3c9a395，schema10、host-01a161c7 epoch0不变。官方MCP精确回读4391bytes，Bridge source_current=true。备份.context-rail/backups/before-grounded-guide-20261023T144121702376Z.sqlite3，回执grounded-rail-live.json；guide revision1/version120为历史，不混作当前。

## 边界与交付

门验证字面覆盖和声明引用，不能证明任意自然语言蕴含；严格改写可因exact要求拒绝。residue有262144bytes/来源与256条上限，loss未覆盖不当成全量。M span对应脱敏note body，不是原文件字节。全库文件元信息generation保守失效，只在调用时核对；同元信息不可见改写不保证识别。旧页legacy_unverified只可历史读取，明确刷新后建立绑定。文件/DB不跨介质原子，版本先写、DB指针权威，孤立版本不当前、投影可修复；外部直接读旧文件的当前性由page工具核对。

最终wheel构建、严格OpenSpec与git diff --cached --check通过，日志.context-rail/grounded-final-build.log。模块提交[b88bb86c7c7d3785de2d9b381019d65213aeeee4](https://github.com/wblxr408/ContextRail/commit/b88bb86c7c7d3785de2d9b381019d65213aeeee4)已推送origin/main，并经GitHub连接器精确回读。wheel不发布，保留用户既有evaluation/research改动。
