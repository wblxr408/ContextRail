# 第一阶段实现与验证

日期：2026-11-01，Asia/Shanghai。仅 tasks 1.1–1.6 完成，后续八项保持未勾选。执行位置为本机隔离 worktree，任务分支 `codex/cm-stage1-20261101`，起点 `c6a05a028a25ce1163fd5bd63dc2cee289c0f5f1`。发布环境 cloud exec 实际返回 `no cloud environments are available for this workspace`，未创建云端任务。

## 交付与复用

| 任务 | 当前交付 | 接入和原 owner |
|---|---|---|
| 1.1 CM01 | 固定 TS 切片、Apache-2.0 LICENSE/NOTICE、逐文件 provenance，随 wheel 打包 | `_vendor/claude_mem`，Python 按需 Node argv |
| 1.2 CM03 | 所有权回执、幂等升级、用户冲突保护、跨文件回滚、client-remove | 原 native_clients 接口与 client-config-backups，client_config 仅映射片段 |
| 1.3 CM14 本批 | 原 renameWithWindowsRetry、UTF-8、中文空格 argv、真实被占用目标回滚 | owned-file-install/atomic-json 原 TS 主体；无 shell/后台 |
| 1.4 CM02/CM04 部分 | 额度/认证/资源/索引/未知原因、原 observed_at/next_retry、生效来源 | 原 job_control/jobs/meta/task_index/prepared，只读 status CLI/MCP |
| 1.5 CM12 | 无模型显式 user 保存、确定性摘录、原文及原 text recall 回读、稳定来源重放/冲突 | 上游 save-memory 结构到原 MemoryRuntime.retain，不增记忆库 |
| 1.6 CM24 本批 | 当前行为记录、依赖与恢复指南、原配置备份附 restore-plan | 本文、CLAUDE_MEM_STAGE1_USE.md、原 TODO |

上游固定 `b80b29bc49d08a71312f44b8feacb2e7585edff4`。直接保留 `replaceOwnedFiles` 暂存/逆序恢复流程与 `renameWithWindowsRetry` 原重试，保留 observer-health 两种接口和纯函数切片、persistMemory 输入验证与 observation 构造。Python 不重写这些核心。原 TS 中 worker、Chroma、云通知、health ledger 不进入闭包。完整补丁列表在 provenance.json。

## TDD 证据

当前消费者契约为 `tests/test_claude_mem_stage1.py`。实现前观察到：

- owned layout 升级被现有同名冲突规则拒绝，预期 upgraded 得到 rejected。
- 后一个 hooks.json 被真实 Windows denied-delete handle 占用时，前一个 TOML 已写入且未恢复。
- 原 quota_exhausted job_control 只被计数，无 quota_deferred 解释。
- memory-save 命令尚不存在，CLI 实际拒绝此次显式保存，返回码 2。

出现的 fixture/接线问题单独修正，不记为功能 RED：QueryBinding 补 schema=1；测试 SQLite 显式关闭以解除 Windows 句柄；测试 JSON 回读指定 UTF-8；离线回读 fixture 补原 checkpoint_lifecycle 依赖；CLI 入口改用其自己的有界 JSON reader。

实现后运行：

```powershell
& 'C:/Users/wblxr/AppData/Roaming/uv/tools/basic-memory/Scripts/python.exe' -m unittest tests.test_claude_mem_stage1 -v
```

**7 项通过，0 失败，0 跳过**；最终隔离工作树一次执行耗时 4.045 秒，仅代表这次行为执行。

1. Codex/Claude 布局升级，单独移除 Claude，保留 Codex 回执及用户 MCP。
2. Windows 锁住后一个目标，早先配置实际回滚，用户 Hook 原样保留。
3. 已 owned 配置升级，保留其他 TOML 值和用户 Hook。
4. 同布局重放没有新增业务配置，CLI 移除只撤销 owned 项。
5. 用户手改 Hook 后移除报冲突，先前 MCP 未被部分删除。
6. 实际原 M Markdown/SQLite：无模型保存、确定性结果、原 text recall 能找回同来源；重放仅一条 memory_event/权威事实；同 ID 异文冲突且原文保留。
7. 原队列只读解释：额度/认证/待索引/失败/未知；过期重试不假称恢复，原控制值未变；Node 缺失时任务仍可读并返回 classifier_unavailable，model_consumption 未知。

## 构建与规格

`uv build --wheel --out-dir <chat>/outputs/package` 成功生成 `contextrail-0.1.0-py3-none-any.whl`。实际构建日志包含所选 `.ts/.mjs`、LICENSE/NOTICE 和 provenance 打包。`openspec.cmd validate claude-mem-self-use-ergonomics --strict` 通过。

未执行旧回归套件、smoke、版本/import/启动/健康探针、措辞检查、哈希/校验和比较、付费模型或泛化性能基准。没有费用、内存占用或长期输入收益的实测结论。仅新增功能需要 Node；原 M 的模型资源控制/退避/捕获监听仍由原 owner 管理。

## 使用和限制

日常命令、受管范围和恢复方法见根目录 CLAUDE_MEM_STAGE1_USE.md。旧无回执等值配置保持未认领，不按名称猜所有权；手改 owned 项需协调后再更新。普通失败逆序恢复，恢复失败保留上游 staging，原备份和 restore-plan 供明确范围恢复；断电/强制终止不承诺多文件原子性。Windows UNC/更多解释器布局未额外验收。

故障分类取自 fixture 中实际记录的原 schema 类别，不能当作本机当前存在额度/认证故障的证据。显式保存为用户声明，未自动调用模型归纳，索引与模型消费保持未知；原文可读不等于 Basic Memory MCP 已确认索引。

本轮没有重新启动当前客户端、写入原生 trust、切换账户/模型、部署第二个 observer，或实施后续阶段。任务分支推送和向 D:/ContextRail 的源目录同步完成情况另见聊天交付记录，当前会话不以源码同步推断已热更新。

向 D:/ContextRail 合并源码后再次仅执行本阶段同一 7 项消费者契约，全部通过（4.198 秒）。CLI 的 native-history/error-history 在途修改保留，未暂存或提交 D 目录的其他工作；原 native 配置、trust 和已安装 M app 未改。此二次执行用于验证实际合并的新入口，不覆盖或验证其他 change。
