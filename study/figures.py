"""Dissertation figures from a study's trials.jsonl.

Greyscale only: the dissertation is printed in black and white, so the
figures must read without colour (hatching and marker shape carry the
distinction, never hue). Every figure is emitted as both PNG (300 dpi, for
the document) and PDF (vector, for a clean rebuild).

    python study/figures.py study/main            # -> study/main/figures/

Figures produced:
  fig_outcomes        stacked outcome breakdown per approach
  fig_mttr            time-to-fix distribution per approach (recovered only)
  fig_safety_coverage the safety-versus-coverage plane: false-fix rate
                      against recovery rate, one point per approach
  fig_recovery_bars   recovery and false-fix rate per approach
"""

from __future__ import annotations

import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 10,
    "axes.edgecolor": "#222",
    "axes.linewidth": 0.8,
    "figure.dpi": 300,
})

ARM_ORDER = ["C0", "C1", "C2", "C3"]
ARM_LABEL = {
    "C0": "C0\nno recovery",
    "C1": "C1\nrestart/rollback",
    "C2": "C2\nAI, ungated",
    "C3": "C3\nAI, gated",
}
# greyscale fills + hatches so outcomes separate in black and white
OUTCOME_STYLE = {
    "recovered":        ("#4d4d4d", ""),
    "refused":          ("#ffffff", "///"),
    "escalated":        ("#b0b0b0", "xxx"),
    "false_fix":        ("#000000", ""),
    "unrecovered":      ("#dddddd", ".."),
    "fault_never_fired":("#ffffff", ""),
    "harness_error":    ("#ffffff", "\\\\\\"),
}
OUTCOME_ORDER = ["recovered", "refused", "escalated", "false_fix",
                 "unrecovered", "fault_never_fired", "harness_error"]
OUTCOME_LABEL = {"recovered": "fixed", "refused": "declined",
                 "escalated": "gave up", "false_fix": "bad fix shipped",
                 "unrecovered": "left broken",
                 "fault_never_fired": "did not trigger",
                 "harness_error": "run error"}


def base_trials(study_dir: str) -> list[dict]:
    """Only the base arms (no ablation variants) enter the headline figures."""
    rows = [json.loads(line)
            for line in open(os.path.join(study_dir, "trials.jsonl"))]
    return [r for r in rows if not r.get("variant")]


def _counts(rows: list[dict], arm: str) -> dict:
    c: dict[str, int] = {}
    for r in rows:
        if r["arm"] == arm:
            c[r["outcome"]] = c.get(r["outcome"], 0) + 1
    return c


def _save(fig, out_dir: str, name: str) -> None:
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out_dir, f"{name}.{ext}"), bbox_inches="tight")
    plt.close(fig)


def fig_outcomes(rows, arms, out_dir):
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    present = [o for o in OUTCOME_ORDER
               if any(_counts(rows, a).get(o) for a in arms)]
    for i, arm in enumerate(arms):
        c = _counts(rows, arm)
        bottom = 0
        for outcome in present:
            n = c.get(outcome, 0)
            if not n:
                continue
            face, hatch = OUTCOME_STYLE[outcome]
            ax.bar(i, n, bottom=bottom, width=0.62, facecolor=face,
                   edgecolor="#222", hatch=hatch, linewidth=0.7)
            bottom += n
    ax.set_xticks(range(len(arms)))
    ax.set_xticklabels([ARM_LABEL[a] for a in arms])
    ax.set_ylabel("trials")
    ax.set_title("Outcome by recovery approach")
    legend = [Patch(facecolor=OUTCOME_STYLE[o][0], edgecolor="#222",
                    hatch=OUTCOME_STYLE[o][1], label=OUTCOME_LABEL[o])
              for o in present]
    ax.legend(handles=legend, fontsize=8, frameon=False,
              loc="center left", bbox_to_anchor=(1.01, 0.5))
    _save(fig, out_dir, "fig_outcomes")


def fig_mttr(rows, arms, out_dir):
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    data, labels = [], []
    for arm in arms:
        vals = [r["mttr_seconds"] for r in rows
                if r["arm"] == arm and r["mttr_seconds"] is not None]
        if vals:
            data.append(vals)
            labels.append(ARM_LABEL[arm])
    if data:
        bp = ax.boxplot(data, tick_labels=labels, patch_artist=True,
                        widths=0.55, medianprops={"color": "#000"})
        for patch in bp["boxes"]:
            patch.set(facecolor="#e0e0e0", edgecolor="#222")
    ax.set_ylabel("time to fix (s)")
    ax.set_title("Time to fix, recovered trials only")
    _save(fig, out_dir, "fig_mttr")


def _rates(rows, arm):
    a = [r for r in rows if r["arm"] == arm]
    n = len(a) or 1
    recovered = sum(1 for r in a if r["outcome"] == "recovered")
    deployed = [r for r in a if r["outcome"] in ("recovered", "false_fix")
                and r.get("detail", {}).get("heal_status") == "deployed"]
    false_fix = sum(1 for r in a if r["outcome"] == "false_fix")
    return {"recovery": recovered / n,
            "false_fix": (false_fix / len(deployed)) if deployed else 0.0,
            "n": len(a)}


def fig_safety_coverage(rows, arms, out_dir):
    fig, ax = plt.subplots(figsize=(5.4, 4.4))
    markers = {"C0": "o", "C1": "s", "C2": "^", "C3": "D"}
    # nudge labels apart when two approaches land on the same point
    placed: list[tuple[float, float]] = []
    for arm in arms:
        r = _rates(rows, arm)
        x, y = r["recovery"], r["false_fix"]
        ax.scatter(x, y, s=95, marker=markers.get(arm, "o"),
                   facecolor="#4d4d4d" if arm != "C3" else "#000",
                   edgecolor="#000", zorder=3)
        dy = 0.012
        while any(abs(x - px) < 0.06 and abs((y + dy) - py) < 0.018
                  for px, py in placed):
            dy += 0.022
        placed.append((x, y + dy))
        ax.annotate(arm, (x, y), (x, y + dy), va="bottom", ha="center",
                    fontsize=10, fontweight="bold")
    ax.set_xlabel("coverage  (recovery rate) → better")
    ax.set_ylabel("risk  (false-fix rate) → worse")
    ax.set_title("Safety versus coverage")
    ax.set_xlim(-0.05, 1.08)
    ax.set_ylim(-0.04, max(0.3, ax.get_ylim()[1]))
    ax.grid(True, linewidth=0.4, color="#ccc", zorder=0)
    ax.scatter([1.0], [0.0], s=140, marker="*", facecolor="none",
               edgecolor="#888", zorder=2)
    ax.annotate("ideal", (1.0, 0.0), (1.0, -0.03), fontsize=8, color="#888",
                ha="center", va="top")
    _save(fig, out_dir, "fig_safety_coverage")


def fig_recovery_bars(rows, arms, out_dir):
    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    x = range(len(arms))
    rec = [_rates(rows, a)["recovery"] for a in arms]
    ff = [_rates(rows, a)["false_fix"] for a in arms]
    ax.bar([i - 0.19 for i in x], rec, width=0.36, facecolor="#4d4d4d",
           edgecolor="#222", label="recovery rate")
    ax.bar([i + 0.19 for i in x], ff, width=0.36, facecolor="#ffffff",
           edgecolor="#222", hatch="///", label="false-fix rate")
    ax.set_xticks(list(x))
    ax.set_xticklabels([ARM_LABEL[a] for a in arms])
    ax.set_ylabel("rate")
    ax.set_ylim(0, 1.05)
    ax.set_title("Recovery and false-fix rate by approach")
    ax.legend(fontsize=8, frameon=False)
    _save(fig, out_dir, "fig_recovery_bars")


def main() -> int:
    study_dir = sys.argv[1] if len(sys.argv) > 1 else "study/main"
    rows = base_trials(study_dir)
    if not rows:
        print(f"no trials in {study_dir}")
        return 1
    arms = [a for a in ARM_ORDER if any(r["arm"] == a for r in rows)]
    out_dir = os.path.join(study_dir, "figures")
    os.makedirs(out_dir, exist_ok=True)

    fig_outcomes(rows, arms, out_dir)
    fig_mttr(rows, arms, out_dir)
    fig_safety_coverage(rows, arms, out_dir)
    fig_recovery_bars(rows, arms, out_dir)
    print(f"wrote 4 figures (png+pdf) to {out_dir} from {len(rows)} base trials")
    return 0


if __name__ == "__main__":
    sys.exit(main())
