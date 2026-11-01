# claude-mem 自用适配：第一阶段

2026-11-01。范围为配置稳定性、故障解释、无模型显式记录。实现与当前行为证据见 `openspec/changes/claude-mem-self-use-ergonomics/verification.md`。压缩后补注入、文件历史、交付物入口、完整界面和更多客户端仍在后续任务中。

## 实际复用与依赖

保留 [claude-mem b80b29b](https://github.com/thedotmack/claude-mem/tree/b80b29bc49d08a71312f44b8feacb2e7585edff4) 的 TypeScript：`replaceOwnedFiles` 暂存/逆序回滚主体、`renameWithWindowsRetry` 原重试函数、observer-health 纯分类、save-memory 输入验证与 observation 构造。归因、Apache-2.0 LICENSE/NOTICE、逐文件补丁见 `contextrail/_vendor/claude_mem/`。原队列、原 M retain、既有确定性摘录/text recall 和 R index/prepared 均继续使用。

新适配功能按需调用 Node 22.6+，带 `--experimental-strip-types`；使用实际 argv、shell=False，Windows 隐藏控制台。无 npm 包安装、Bun、HTTP worker、后台 observer 或新记忆数据库。可用 `CONTEXTRAIL_NODE` 指定受信任的 Node 可执行文件；不会改 PATH 或自动下载。Node 缺失时配置/显式保存给出依赖错误，status 保留原任务及队列视图，并显示 classifier_unavailable。

显式保存还依赖现有工程记忆 `home/app/memory_runtime.py` 与其原依赖、`home/config.json` 的既有 vault。沿用原运行环境（本机为 Basic Memory 专用 Python），以 `use_models=False` 调用 M，不启动模型。保存到原 Markdown 权威目录和原 state.sqlite 缓存，返回路径并不表示已由 Basic Memory MCP 索引确认。

## 配置应用、升级与移除

继续使用原 `client-prepare` 生成匹配 workspace 的布局，再执行：

```powershell
python -m contextrail client-apply --workspace D:/my-project --layout D:/my-project/.context-rail/clients/v1
python -m contextrail client-apply --workspace D:/my-project --layout D:/my-project/.context-rail/clients/v2
python -m contextrail client-remove --workspace D:/my-project --client codex
```

应用把新加入的 MCP 项、精确 TOML 块和 Hook 组登记到 `.context-rail/client-install.json`。同布局重放不增项。更换布局只迁移回执匹配的旧项；人工编辑、重复 owned Hook 或并发修改报冲突，保留原配置。Codex TOML 的其他块和注释保留，JSON 保留其他字段和 Hook。

旧安装没有回执时，等值条目保持未认领，异值条目拒绝覆盖。不能根据 contextrail 名称自动认领或删除；需要先人工核对来源并协调旧条目。回执不是原生 Hook 信任，生效仍遵守原客户端机制。

多文件变更和回执一并送入上游事务。某目标被 Windows 占用时，原代码执行有界重试，随后回滚已替换部分；逆序恢复失败则保留 recovery_dir。原 `.context-rail/client-config-backups/<operation>/` 保存修改前内容及 `restore-plan.json`。普通失败提示附上恢复位置。进程强制终止/断电不承诺跨文件原子性，需依备份和计划人工协调；不会按失败提示自动删除用户文件。

`.context-rail/client-config.lock` 只用于互斥配置修改。遇到已有锁时，先核实原操作是否结束，再显式处理遗留锁，不自动夺取。`client-remove` 只撤销回执匹配项，可能留下有效空配置文件和空回执，以保留用户内容。

## 故障解释与生效来源

沿用 `status --query-binding <trusted.json>` 和只读 MCP `context.status`。原可信 QueryBinding 指定 exact task/workspace、memory_home、native_sessions 和 memory_mode；model 参数不能覆盖这些绑定。CLI/MCP 宿主额外挂载原 project.describe 的 config/sources 和字节预算。

新增 diagnostics 读取原 jobs/job_control/meta，保留来源标记和 next_retry。主要代码为 quota_deferred、credential_rejected、setup_required、resource_deferred、index_unavailable、awaiting_index、failed_jobs，以及无法确定原因时的 queue_deferred。只有原库明确记录类别时才解释，未知原错误正文不透传。共享退避标为 shared_runtime_not_task。

额度限制不建议重启；已过重试时间显示 last_observed_wait_retry_due 和 recovery=not_observed。原 jobs 的 saved、捕获原文、索引状态和 prepared receipt 分别显示。进程存活与模型消费保持 unknown。无绑定、读不到库或缺旧 schema 的细节均明确显示；不会创建空库、重新学习或重放失败任务。完整输出仍按既有字节预算返回，必要时标 display_truncated。

## 无模型显式记事

这是可信宿主 CLI，不新增 Rail MCP 写工具。先准备 UTF-8 JSON：

```json
{"event_id":"preference-20261101-1","title":"交付偏好","text":"只有我明确要求时才发布。","role":"user"}
```

```powershell
& 'C:/Users/wblxr/AppData/Roaming/uv/tools/basic-memory/Scripts/python.exe' -m contextrail memory-save --workspace D:/my-project --home C:/Users/wblxr/.codex/services/engineering-memory --input D:/my-project/record.json
```

可用 `--input -` 从 stdin 读取。text 最大 32 KiB，title 最多 256 字符，输入最多 64 KiB。入口只接显式 user 文本，先走原脱敏边界，再用上游规范化，最后调用 M.retain；不把 assistant/tool 历史当作用户记录。

event_id 在可信 workspace 内稳定绑定正文；网络/调用重试继续用同一 ID，一次业务效果。用同一 ID 提交不同文字会报 conflict，新纠正应给新 ID。并发显式操作使用现有 home 内的 `explicit-memory.lock`，不自动抢锁。

结果包含 source 原 Markdown 路径、duplicate、确定性 brief，以及原 memory_recall / MemoryRuntime.recall(mode=text) 调用参数。可直接打开 source 回读原文；已有 R brief/explain 保留原入口。learning_state=explicit_user_record_not_model_derived，index_state=not_observed，model_consumption=unknown。明确用户记录没有自动触发模型归纳；text recall 仅关键词/已有关系覆盖，不能声称语义检索。

## 验证与交付边界

只运行本阶段新增消费者行为契约；实际覆盖 Windows 中文/空格目录、文件占用导致的回滚、Codex/Claude 升级与精确移除、故障分类和无模型保存/重放/冲突/text 回读。没有运行旧回归套件、探针或费用/内存基准。

本轮执行位置为本地隔离任务 worktree；云端提交实际返回环境不可用。分支交付与本机源目录同步、当前已打开客户端是否重启/生效分别记录，不能仅凭本文件推断原生会话已热更新。
