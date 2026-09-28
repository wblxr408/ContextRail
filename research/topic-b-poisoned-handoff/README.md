# Topic B — The Poisoned Handoff: Adversarial Non-Native Trajectories at the Model-Switch Boundary

状态：立项中（2026-09-23）。研究选题文件，不是已完成工作。

## 一句话命题

模型切换时，接收模型必须续写它没有创作的轨迹。已有工作（良性地）证明继承弱模型
轨迹会降质、丢弃它能改善升级。本题把这个边界当作**对抗攻击面**：弱/被攻陷的模型
写下轨迹，操纵下游更强模型。ContextRail 的 discard-trace-on-switch 是天然命名防御。

## 真实 gap 出处（anchor 论文 live 核实）

| 论文 | arXiv | 状态 |
|---|---|---|
| The Handoff Tax | 2608.24358 | live 核实：证明"全轨迹升级只挽回<一半能力差距+多付成本"、"减少弱模型轨迹提升升级质量"；**abstract 无 adversarial 字样，只测 cost/quality** |
| Performance Drift from Model Switching | 2603.03111 | live 核实：switch-matrix，"prefix influence"是因果杠杆；**只测良性切换，无 adversarial** |

近邻（须显式区分，均 live 核实）：
- Verification-Status Laundering 2609.20211：handoff 丢授权语境——框成授权洗白，非能力差 / 非轨迹注入。
- Hidden in Thought 2607.15286：受损模型 CoT 注入其他模型——框成越狱迁移，非 tier 降级切换。

裁决：把"弱→强继承轨迹"当**攻击面**并建药方，UNCLAIMED。两篇 anchor 证明结构良性存在，
对抗版无人做——这是最干净的"结构已存在只差对抗框架"的 gap。

## 威胁模型

- 弱/低成本层模型（或被攻陷的）在升级切换前写推理轨迹。
- 攻击者经被继承的轨迹（嵌入指令、投毒的中间"事实"、伪造工具结果）操纵下游更强模型。
- 关键不对称（须遵 Handoff Tax）：升级丢弃弱轨迹是防御；降级保留强轨迹别丢。

## ContextRail 为何是可信研究载体

- discard-trace-on-switch = 结构性隔离：非原生轨迹整体丢弃，只有版本化、完整性校验过的
  任务真相过 handoff 边界（`prepare→acknowledge→validate→activate` + epoch fencing）。
- 把 Handoff Tax 的良性质量启发升级成一个**命名的安全控制**。
- 可直接复用现有 `contextrail/evaluation/switching.py` 实验框架（工程增量最小）。

## 最小实验

1. 在 Handoff Tax / switch-matrix 协议上加一条"对抗前缀"臂。
2. 量三种下游被攻陷率：(a) 全轨迹继承 (b) 压缩 (c) 丢轨迹留版本化真相（ContextRail）。
3. 假设：(c) 崩掉攻击成功率，同时保住 Handoff Tax 的质量收益。跨厂商 DeepSeek+Kimi 或先单厂商通管线。

## 候选会议（详见 ../VENUE-ASSESSMENT.md）

- 主：AAMAS（agent 安全，CCF-B/CORE-A，2027-05 Hanoi，6 月前参会 ✓）或安全类（USENIX/CCS/NDSS/S&P）。
- 不适合：FSE（对称密码，无关）、CSF（形式化基础，本题偏经验对抗评测）。

## 诚实边界

攻击成功率、任务成功率都是可执行指标。novelty 精确在"对抗框架 + discard-as-defense"，
不是发明 handoff 现象（那是 2608.24358）。须 scope：discard 只对升级/弱源成立
（Reasoning Relay 2512.20647 / Woodpecker 2608.05168 证明强轨迹常帮弱受体）。
