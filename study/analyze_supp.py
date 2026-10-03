"""Analysis of the supplementary study (pre-registration amendment A2).

Reads study/supp/{practice_bank.jsonl,trials.jsonl,kill_matrix.json} and
the main study's C3 trials (the no-memory baseline), writes
study/supp/analysis.json and the hardening figure.

    python study/analyze_supp.py
"""

from __future__ import annotations

import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "study"))

from analyze import (SEED, bootstrap_median_ci, cliffs_delta,  # noqa: E402
                     fisher_exact, holm, wilcoxon_signed_rank)

SUPP = os.path.join(ROOT, "study", "supp")
MAIN = os.path.join(ROOT, "study", "main")


def _rows(path: str) -> list[dict]:
    return [json.loads(line) for line in open(path)] if os.path.exists(path) else []


def first_attempt(t: dict) -> bool:
    return t["outcome"] == "recovered" and t["detail"].get("candidates") == 1


def arm_summary(rs: list[dict]) -> dict:
    rep = [t for t in rs if t["expected_c3"] == "healed"]
    mttr = [t["mttr_seconds"] for t in rs if t["outcome"] == "recovered"]
    outcomes: dict[str, int] = {}
    for t in rs:
        outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1
    return {"n": len(rs), "outcomes": outcomes,
            "repairable_n": len(rep),
            "first_attempt": sum(first_attempt(t) for t in rep),
            "first_attempt_rate": round(sum(first_attempt(t) for t in rep)
                                        / len(rep), 3) if rep else None,
            "recovered_repairable": sum(t["outcome"] == "recovered" for t in rep),
            "recovery_rate": round(sum(t["outcome"] == "recovered" for t in rs)
                                   / len(rs), 3) if rs else None,
            "false_fix": outcomes.get("false_fix", 0),
            "mean_candidates_recovered": round(
                sum(t["detail"].get("candidates", 0) for t in rep
                    if t["outcome"] == "recovered")
                / max(1, sum(t["outcome"] == "recovered" for t in rep)), 3),
            "mttr": bootstrap_median_ci(mttr) if mttr else None}


def per_fault_median(rs: list[dict]) -> dict[str, float]:
    by: dict[str, list[float]] = {}
    for t in rs:
        if t["outcome"] == "recovered":
            by.setdefault(t["entry"], []).append(t["mttr_seconds"])
    return {k: sorted(v)[len(v) // 2] for k, v in by.items()}


def compare(a: list[dict], b: list[dict]) -> dict:
    sa, sb = arm_summary(a), arm_summary(b)
    fa, fb = sa["first_attempt"], sb["first_attempt"]
    fisher = fisher_exact(fa, sa["repairable_n"] - fa, fb, sb["repairable_n"] - fb)
    ma, mb = per_fault_median(a), per_fault_median(b)
    common = sorted(set(ma) & set(mb))
    diffs = [ma[k] - mb[k] for k in common]
    return {"first_attempt": [fa, sa["repairable_n"], fb, sb["repairable_n"]],
            "first_attempt_fisher_p": fisher,
            "mttr_paired_faults": len(common),
            "mttr_wilcoxon": wilcoxon_signed_rank(diffs) if diffs else None,
            "mttr_cliffs_delta": round(cliffs_delta([ma[k] for k in common],
                                                    [mb[k] for k in common]), 3)
            if common else None}


def hardening(bank: list[dict], km: dict) -> dict:
    """Mutation-score growth over the rehearsal sequence, and recurrence."""
    killed_by = {m: set(v or []) for m, v in km["killed_by"].items()}
    mutants = sorted(killed_by)
    valid = set(km["banked_tests"]) - set(km["invalid_on_clean"])

    def score(tests: set[str]) -> int:
        return sum(1 for m in mutants if killed_by[m] & tests)

    order = []
    for row in sorted(bank, key=lambda r: r["rehearsed_at"]):
        t = row["entry_id"].lower()
        order.append(t if t in valid else None)
    curve, have = [], set()
    for i, t in enumerate(order, 1):
        if t:
            have.add(t)
        curve.append({"rehearsals": i, "suite_size": len(have),
                      "killed": score(have),
                      "score": round(score(have) / len(mutants), 3)})

    # robustness: mean curve over random rehearsal orders
    rng = random.Random(SEED)
    n_perm, sums = 1000, [0.0] * len(order)
    for _ in range(n_perm):
        perm = order[:]
        rng.shuffle(perm)
        have = set()
        for i, t in enumerate(perm):
            if t:
                have.add(t)
            sums[i] += score(have) / len(mutants)
    mean_curve = [round(s / n_perm, 3) for s in sums]

    healed = sorted(t.upper() for t in valid)
    self_blocked = [e for e in healed if e.lower() in killed_by.get(e, set())]
    cross = [m for m in mutants if killed_by[m] - {m.lower()}]
    return {"mutants": len(mutants), "banked_tests": len(km["banked_tests"]),
            "valid_tests": len(valid), "invalid_on_clean": km["invalid_on_clean"],
            "curve": curve, "mean_curve_random_order": mean_curve,
            "final_score": curve[-1]["score"] if curve else 0.0,
            "recurrence": {
                "healed_faults": len(healed),
                "blocked_on_redeploy": len(self_blocked),
                "recurrence_rate_with_bank": round(
                    1 - len(self_blocked) / len(healed), 3) if healed else None,
                "recurrence_rate_without_bank": 1.0,
                "not_blocked": sorted(set(healed) - set(self_blocked))},
            "cross_protection": {
                "mutants_killed_by_another_faults_test": len(cross),
                "which": cross}}


def figure(h: dict, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "serif", "font.size": 10})
    xs = [c["rehearsals"] for c in h["curve"]]
    fig, ax = plt.subplots(figsize=(6, 3.6))
    ax.plot(xs, [c["score"] for c in h["curve"]], color="black",
            label="rehearsal order as run")
    ax.plot(xs, h["mean_curve_random_order"], color="grey", linestyle="--",
            label="mean over 1,000 random orders")
    ax.set_xlabel("rehearsals completed")
    ax.set_ylabel("mutation score of banked suite")
    ax.set_ylim(0, 1)
    ax.set_title("Hardening: the banked suite over the corpus mutant pool")
    ax.grid(color="#dddddd", linewidth=0.6)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(f"{path}.{ext}", dpi=300)


def main() -> int:
    bank = _rows(os.path.join(SUPP, "practice_bank.jsonl"))
    trials = _rows(os.path.join(SUPP, "trials.jsonl"))
    base = [t for t in _rows(os.path.join(MAIN, "trials.jsonl"))
            if t["arm"] == "C3" and not t["variant"]]
    mem = [t for t in trials if t["variant"] == "C3-mem"]
    loo = [t for t in trials if t["variant"] == "C3-mem-loo"]

    status: dict[str, int] = {}
    for r in bank:
        status[r["status"]] = status.get(r["status"], 0) + 1
    keys: dict[str, set[str]] = {}
    for r in bank:
        if r["status"] == "would_heal" and r.get("key"):
            keys.setdefault(r["key"], set()).add(r["entry_id"])
    with_sibling = sorted({e for ids in keys.values() if len(ids) > 1
                           for e in ids})

    out = {"practice": {"rehearsals": len(bank), "status": status,
                        "shape_keys_healed": len(keys),
                        "entries_with_healed_sibling": with_sibling},
           "arms": {"C3 (no memory, main study)": arm_summary(base),
                    "C3-mem": arm_summary(mem),
                    "C3-mem-loo": arm_summary(loo)},
           "comparisons": {}}
    if mem:
        out["comparisons"]["H4: C3-mem vs C3"] = compare(mem, base)
    if loo:
        out["comparisons"]["H5c: C3-mem-loo vs C3"] = compare(loo, base)
        sib = set(with_sibling)
        out["comparisons"]["H5c (exploratory, faults with a healed sibling)"] = \
            compare([t for t in loo if t["entry"] in sib],
                    [t for t in base if t["entry"] in sib])
    ps = {k: v["first_attempt_fisher_p"] for k, v in out["comparisons"].items()
          if "exploratory" not in k}
    out["holm_adjusted_first_attempt_p"] = holm(ps) if ps else {}

    km_path = os.path.join(SUPP, "kill_matrix.json")
    if os.path.exists(km_path):
        out["hardening"] = hardening(bank, json.load(open(km_path)))
        os.makedirs(os.path.join(SUPP, "figures"), exist_ok=True)
        figure(out["hardening"], os.path.join(SUPP, "figures", "fig_hardening"))

    with open(os.path.join(SUPP, "analysis.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "hardening"},
                     indent=1)[:4000])
    if "hardening" in out:
        h = out["hardening"]
        print("hardening:", {k: h[k] for k in ("mutants", "valid_tests",
                                               "final_score", "recurrence",
                                               "cross_protection")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
