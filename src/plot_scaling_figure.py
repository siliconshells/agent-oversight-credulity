"""
plot_scaling_figure.py — the paper's Figure 2: fabricated_observation detection vs overseer
tier, one line per protocol, with the bootstrap 95% CIs from detection.csv as error bars.

Reads  ../results/run2_scaling/detection.csv   (written by run_experiment.py)
Writes ../results/run2_scaling/scaling_fabricated_observation.png

Run (from this src/ directory):
    python plot_scaling_figure.py              # uses ../results/run2_scaling
    python plot_scaling_figure.py <run_dir>
"""
from __future__ import annotations
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "results", "run2_scaling")

TIERS = ["weak", "mid", "strong"]
TIER_LABELS = ["weak\n(Haiku 4.5)", "mid\n(Sonnet 4.6)", "strong\n(Opus 4.8)"]

# Same protocol -> color mapping as Figure 1 (detection_by_class_*.png). Orange and green are
# hard to tell apart with protanopia, so each protocol also gets its own marker and line style.
STYLE = {
    "judge_alone": dict(color="#d62728", marker="o", ls="-"),
    "consultancy": dict(color="#1f77b4", marker="s", ls="--"),
    "critique":    dict(color="#ff7f0e", marker="^", ls="-"),
    "debate":      dict(color="#2ca02c", marker="D", ls=":"),
}
# small horizontal offsets so coincident points (e.g. several protocols at 1.00) stay visible
OFFSET = {"judge_alone": 0.0, "consultancy": -0.035, "critique": 0.0, "debate": 0.035}

TITLE = ("Structured assistance collapses as the overseer scales\n"
         "Unaided overseer is at the floor at every tier (the structural gap);\n"
         "assistance helps a weak overseer but vanishes for a strong one")


def main() -> None:
    det = pd.read_csv(os.path.join(RUN, "detection.csv"))
    fab = det[det["flaw_type"] == "fabricated_observation"]

    fig, ax = plt.subplots(figsize=(7.2, 4.8))
    ax.axhline(0.5, lw=0.8, color="#c8c8c8", zorder=0)          # chance level
    for proto, st in STYLE.items():
        s = fab[fab["protocol"] == proto].set_index("judge_level").reindex(TIERS)
        if s["detection"].isna().all():
            continue
        x = [i + OFFSET[proto] for i in range(len(TIERS))]
        y = s["detection"].to_numpy(dtype=float)
        yerr = [y - s["ci_lo"].to_numpy(dtype=float), s["ci_hi"].to_numpy(dtype=float) - y]
        ax.errorbar(x, y, yerr=yerr, color=st["color"], ls=st["ls"], lw=2,
                    marker=st["marker"], ms=7, mec="white", mew=1.2,
                    capsize=3, elinewidth=1.2, label=proto, zorder=3)

    ax.set_xticks(range(len(TIERS)))
    ax.set_xticklabels(TIER_LABELS)
    ax.set_xlim(-0.2, len(TIERS) - 0.8)
    ax.set_ylim(-0.04, 1.06)
    ax.set_ylabel("detection accuracy (fabricated tool outputs)")
    ax.set_title(TITLE, fontsize=11)
    ax.legend(loc="upper right", frameon=True)
    fig.tight_layout()
    out = os.path.join(RUN, "scaling_fabricated_observation.png")
    fig.savefig(out, dpi=140)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
