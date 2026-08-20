"""Pre-registered analysis over a trials.jsonl.

Implements exactly the plan frozen in study/preregistration.md: per-arm
outcome rates, median MTTR with 10,000-resample bootstrap 95% CIs,
Wilcoxon signed-rank on paired MTTR differences over faults recovered by
both arms (exact for n <= 20, normal approximation above), two-sided
Fisher exact tests for rate differences, Holm correction across the
family, and Cliff's delta effect sizes. Stdlib only, seeded, so the
analysis is replayable byte for byte.

    python study/analyze.py study/pilot [--json out.json]

Run against pilot data it labels the output PILOT: NOT FOR INFERENCE;
the pre-registration excludes pilot data from the main analysis.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys

BOOTSTRAP_N = 10_000
SEED = 259074235          # the registered seed: the candidate's matric number


# ---------------------------------------------------------------- statistics

def bootstrap_median_ci(values: list[float], seed: int = SEED) -> dict:
    rng = random.Random(seed)
    n = len(values)
    medians = sorted(
        statistics.median(rng.choices(values, k=n)) for _ in range(BOOTSTRAP_N))
    return {"median": round(statistics.median(values), 3),
            "ci95": [round(medians[int(0.025 * BOOTSTRAP_N)], 3),
                     round(medians[int(0.975 * BOOTSTRAP_N) - 1], 3)],
            "n": n}


def wilcoxon_signed_rank(diffs: list[float]) -> dict:
    """Two-sided signed-rank test; zeros dropped, average ranks for ties."""
    nonzero = [d for d in diffs if d != 0]
    n = len(nonzero)
    if n < 5:
        return {"n": n, "p": None, "note": "fewer than 5 non-zero pairs"}
    ranked = sorted((abs(d), i) for i, d in enumerate(nonzero))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ranked[j + 1][0] == ranked[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[ranked[k][1]] = avg
        i = j + 1
    w_plus = sum(r for r, d in zip(ranks, nonzero) if d > 0)

    if n <= 20:               # exact distribution by enumeration
        total = 0
        extreme = 0
        w_mean = n * (n + 1) / 4
        observed_dev = abs(w_plus - w_mean)
        for mask in range(1 << n):
            w = sum(ranks[b] for b in range(n) if mask >> b & 1)
            total += 1
            if abs(w - w_mean) >= observed_dev - 1e-9:
                extreme += 1
        p = extreme / total
        method = "exact"
    else:                     # normal approximation with continuity correction
        mean = n * (n + 1) / 4
        sd = math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
        z = (abs(w_plus - mean) - 0.5) / sd
        p = 2 * (1 - _phi(z))
        method = "normal-approx"
    return {"n": n, "W": w_plus, "p": round(min(p, 1.0), 5), "method": method}


def _phi(z: float) -> float:
    return (1 + math.erf(z / math.sqrt(2))) / 2


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact for [[a, b], [c, d]]."""
    def hyper(a_):
        return (math.comb(a + b, a_) * math.comb(c + d, (a + c) - a_)
                / math.comb(a + b + c + d, a + c))
    p_obs = hyper(a)
    p = 0.0
    for a_ in range(max(0, a + c - (c + d)), min(a + b, a + c) + 1):
        p_a = hyper(a_)
        if p_a <= p_obs + 1e-12:
            p += p_a
    return round(min(p, 1.0), 5)


def cliffs_delta(xs: list[float], ys: list[float]) -> float:
    if not xs or not ys:
        return 0.0
    gt = sum(1 for x in xs for y in ys if x > y)
    lt = sum(1 for x in xs for y in ys if x < y)
    return round((gt - lt) / (len(xs) * len(ys)), 3)


def holm(named_ps: dict[str, float]) -> dict[str, float]:
    items = sorted(((p, name) for name, p in named_ps.items()
                    if p is not None))
    adjusted: dict[str, float] = {}
    m = len(items)
    running = 0.0
    for rank, (p, name) in enumerate(items):
        adj = min(1.0, (m - rank) * p)
        running = max(running, adj)
        adjusted[name] = round(running, 5)
    return adjusted


# ------------------------------------------------------------------ analysis

def analyse(trials: list[dict]) -> dict:
    # the four-arm comparison is over base trials only; ablation variants
    # carry arm "C3" too and would otherwise contaminate the C3 figures.
    base = [t for t in trials if not t.get("variant")]
    arms = sorted({t["arm"] for t in base})
    result: dict = {"trials": len(base),
                    "trials_including_ablations": len(trials),
                    "per_arm": {}}

    def rows(arm):
        return [t for t in base if t["arm"] == arm]

    for arm in arms:
        rs = rows(arm)
        outcomes: dict[str, int] = {}
        for r in rs:
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
        mttrs = [r["mttr_seconds"] for r in rs if r["mttr_seconds"] is not None]
        deployed = [r for r in rs
                    if r["outcome"] in ("recovered", "false_fix")
                    and r.get("detail", {}).get("heal_status") == "deployed"]
        result["per_arm"][arm] = {
            "outcomes": outcomes,
            "recovery_rate": round(outcomes.get("recovered", 0) / len(rs), 3),
            "false_fix_rate": round(
                outcomes.get("false_fix", 0) / len(deployed), 3)
            if deployed else None,
            "escalation_rate": round(
                (outcomes.get("escalated", 0) + outcomes.get("refused", 0))
                / len(rs), 3),
            "mttr": bootstrap_median_ci(mttrs) if mttrs else None,
        }

    # paired comparisons on faults recovered by both arms
    comparisons: dict[str, dict] = {}
    ps: dict[str, float] = {}
    for arm_a, arm_b in [("C3", "C1"), ("C3", "C2"), ("C2", "C1")]:
        if arm_a not in arms or arm_b not in arms:
            continue
        a_by = {t["entry"]: t for t in rows(arm_a)}
        b_by = {t["entry"]: t for t in rows(arm_b)}
        both = [e for e in a_by
                if e in b_by
                and a_by[e]["mttr_seconds"] is not None
                and b_by[e]["mttr_seconds"] is not None]
        diffs = [a_by[e]["mttr_seconds"] - b_by[e]["mttr_seconds"]
                 for e in both]
        test = wilcoxon_signed_rank(diffs)
        name = f"mttr:{arm_a}-vs-{arm_b}"
        comparisons[name] = {
            "paired_faults": len(both), "wilcoxon": test,
            "cliffs_delta": cliffs_delta(
                [a_by[e]["mttr_seconds"] for e in both],
                [b_by[e]["mttr_seconds"] for e in both])}
        if test.get("p") is not None:
            ps[name] = test["p"]

    # rate comparisons (Fisher exact): recovery C3 vs C2, false fix C3 vs C2
    for metric, outcome in [("recovery", "recovered"),
                            ("false_fix", "false_fix")]:
        if "C3" in arms and "C2" in arms:
            a = sum(1 for t in rows("C3") if t["outcome"] == outcome)
            b = len(rows("C3")) - a
            c = sum(1 for t in rows("C2") if t["outcome"] == outcome)
            d = len(rows("C2")) - c
            p = fisher_exact(a, b, c, d)
            name = f"{metric}:C3-vs-C2"
            comparisons[name] = {"table": [[a, b], [c, d]], "fisher_p": p}
            ps[name] = p

    result["comparisons"] = comparisons
    result["holm_adjusted_p"] = holm(ps)

    # ablations: the gated arm with one mechanism disabled, versus full C3.
    # Reported descriptively (recovery rate, false-fix rate, median MTTR),
    # since each isolates a single mechanism's contribution.
    variants = sorted({t["variant"] for t in trials if t.get("variant")})
    if variants:
        full = [t for t in base if t["arm"] == "C3"]
        abl: dict = {"C3 (full)": _arm_summary(full)}
        for v in variants:
            abl[v] = _arm_summary([t for t in trials if t.get("variant") == v])
        result["ablations"] = abl
    return result


def _arm_summary(rs: list[dict]) -> dict:
    n = len(rs) or 1
    recovered = sum(1 for r in rs if r["outcome"] == "recovered")
    deployed = [r for r in rs if r["outcome"] in ("recovered", "false_fix")
                and r.get("detail", {}).get("heal_status") == "deployed"]
    false_fix = sum(1 for r in rs if r["outcome"] == "false_fix")
    mttrs = [r["mttr_seconds"] for r in rs if r["mttr_seconds"] is not None]
    return {"n": len(rs),
            "recovery_rate": round(recovered / n, 3),
            "false_fix_rate": round(false_fix / len(deployed), 3)
            if deployed else None,
            "median_mttr": round(statistics.median(mttrs), 2) if mttrs else None}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("study_dir")
    parser.add_argument("--json", dest="out")
    args = parser.parse_args()

    trials_path = os.path.join(args.study_dir, "trials.jsonl")
    trials = [json.loads(line) for line in open(trials_path)]
    result = analyse(trials)

    meta_path = os.path.join(args.study_dir, "meta.json")
    if os.path.exists(meta_path):
        result["meta"] = json.load(open(meta_path))
    if "pilot" in os.path.abspath(args.study_dir):
        result["WARNING"] = ("PILOT DATA: NOT FOR INFERENCE. The "
                             "pre-registration excludes pilot data from the "
                             "main analysis; this run is a dry run of the "
                             "pipeline only.")

    out = args.out or os.path.join(args.study_dir, "analysis.json")
    with open(out, "w") as fh:
        json.dump(result, fh, indent=2)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
