"""Render the semantic C0/C1/C2 ablation as a static PNG (matplotlib, Agg).

Reads ``ablation.json`` produced by :mod:`semantic_ablation` and draws two
panels: dependency-closure completeness per strategy, and packet bytes per
task.  It imports matplotlib lazily so the core evaluation never hard-depends
on it; a missing matplotlib degrades to a clear message, not an import error.
"""

from __future__ import annotations

import json
from pathlib import Path


def render_ablation_chart(run_dir: Path, *, out_name: str = "ablation-chart.png") -> Path:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - environment without matplotlib
        raise RuntimeError("matplotlib is required to render the ablation chart; install it first.") from exc

    # Pick the first installed CJK-capable font so Chinese labels render as
    # glyphs, not tofu boxes.  Fall back silently to the default if none exist.
    import matplotlib.font_manager as fm
    for family in ("Microsoft YaHei", "SimHei", "Microsoft JhengHei", "SimSun", "DengXian"):
        try:
            if fm.findfont(fm.FontProperties(family=family), fallback_to_default=False):
                matplotlib.rcParams["font.sans-serif"] = [family]
                break
        except Exception:
            continue
    matplotlib.rcParams["axes.unicode_minus"] = False

    data = json.loads((run_dir / "ablation.json").read_text(encoding="utf-8"))
    tasks = data["tasks"]
    strategies = ("C0", "C1", "C2")
    colors = {"C0": "#8892b0", "C1": "#4c9fce", "C2": "#2e9e6b"}

    complete = {s: sum(1 for t in tasks if t["outcomes"][s]["closure_complete"]) for s in strategies}
    total = len(tasks)

    fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(11, 4.3))
    fig.suptitle("语义规划层 C0/C1/C2 消融（确定性机制验证，无模型调用）", fontsize=12, fontweight="bold")

    # Left: closure completeness per strategy.
    bars = ax_left.bar(strategies, [complete[s] for s in strategies],
                       color=[colors[s] for s in strategies], width=0.55)
    ax_left.set_ylim(0, total + 0.5)
    ax_left.set_ylabel(f"依赖闭包完整的任务数（共 {total}）")
    ax_left.set_title("完整率：C2 成组装入消除静默遗漏")
    for bar, s in zip(bars, strategies):
        ax_left.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                     f"{complete[s]}/{total}", ha="center", va="bottom", fontsize=10)

    # Right: packet bytes per task, grouped by strategy.
    labels = [t["task_id"] for t in tasks]
    x = range(len(labels))
    width = 0.26
    for offset, s in zip((-1, 0, 1), strategies):
        heights = [t["outcomes"][s]["packet_bytes"] for t in tasks]
        ax_right.bar([i + offset * width for i in x], heights, width=width,
                     label=s, color=colors[s])
    ax_right.set_xticks(list(x))
    ax_right.set_xticklabels(labels)
    ax_right.set_ylabel("Packet 字节（越小越省，但完整性优先）")
    ax_right.set_title("每任务 packet 体积")
    ax_right.legend(title="策略")

    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out_path = run_dir / out_name
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path
