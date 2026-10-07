"""Analysis of the weaker-model study (pre-registration amendment A3).

Exactly the analyses A3 registered: the false-fix rate, C2 against C3
(two-sided Fisher exact, the H2 test); and as secondary measures, recovery
on the 45 repairable faults, refusal correctness, first-attempt admission,
MTTR (Wilcoxon, paired per fault) and why rejected C3 candidates failed.
The main study's Opus figures are printed alongside for comparison.

    python study/analyze_haiku.py      -> study/haiku/analysis.json
"""

from __future__ import annotations

import collections
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "study"))
from analyze import bootstrap_median_ci, cliffs_delta, fisher_exact, wilcoxon_signed_rank  # noqa: E402

HAIKU = os.path.join(ROOT, "study", "haiku")
MAIN = os.path.join(ROOT, "study", "main")


def rows(path):
    return [json.loads(l) for l in open(path)] if os.path.exists(path) else []


def summary(rs):
    rep = [t for t in rs if t["expected_c3"] == "healed"]
    patched = [t for t in rs if t["outcome"] in ("recovered", "false_fix")
               and (t.get("detail") or {}).get("candidates")]
    mttr = [t["mttr_seconds"] for t in rs if t["outcome"] == "recovered"]
    out = collections.Counter(t["outcome"] for t in rs)
    return {"n": len(rs), "outcomes": dict(out),
            "false_fix": out.get("false_fix", 0),
            "false_fix_rate": round(out.get("false_fix", 0) / len(patched), 3) if patched else None,
            "patched": len(patched),
            "recovered_repairable": sum(t["outcome"] == "recovered" for t in rep),
            "repairable": len(rep),
            "first_attempt": sum(t["outcome"] == "recovered" and (t.get("detail") or {}).get("candidates") == 1
                                 for t in rep),
            "refused": out.get("refused", 0),
            "refused_correctly": sum(t["outcome"] == "refused" and t["expected_c3"] == "refused" for t in rs),
            "mttr": bootstrap_median_ci(mttr) if mttr else None}


def per_fault(rs):
    by = collections.defaultdict(list)
    for t in rs:
        if t["outcome"] == "recovered":
            by[t["entry"]].append(t["mttr_seconds"])
    return {k: sorted(v)[len(v) // 2] for k, v in by.items()}


def rejection_reasons(cert_dir):
    reasons = collections.Counter()
    for p in glob.glob(os.path.join(cert_dir, "*.json")):
        for cand in json.load(open(p)).get("candidates", []):
            conds = cand.get("conditions") or {}
            if conds and not all(conds.values()):
                reasons[next(k for k, v in conds.items() if not v)] += 1
    return dict(reasons)


def main() -> int:
    trials = rows(os.path.join(HAIKU, "trials.jsonl"))
    c2 = [t for t in trials if t["arm"] == "C2"]
    c3 = [t for t in trials if t["arm"] == "C3"]
    s2, s3 = summary(c2), summary(c3)
    out = {"n_trials": len(trials), "model": "claude-haiku-4-5-20251001",
           "C2": s2, "C3": s3,
           "H2_false_fix_fisher_p": fisher_exact(s3["false_fix"], s3["patched"] - s3["false_fix"],
                                                 s2["false_fix"], s2["patched"] - s2["false_fix"]),
           "recovery_repairable_fisher_p": fisher_exact(
               s3["recovered_repairable"], s3["repairable"] - s3["recovered_repairable"],
               s2["recovered_repairable"], s2["repairable"] - s2["recovered_repairable"]),
           "c3_rejections_by_first_failed_condition": rejection_reasons(os.path.join(HAIKU, "certificates"))}
    m2, m3 = per_fault(c2), per_fault(c3)
    common = sorted(set(m2) & set(m3))
    if common:
        out["mttr_c3_vs_c2"] = {"paired_faults": len(common),
                                "wilcoxon": wilcoxon_signed_rank([m3[k] - m2[k] for k in common]),
                                "cliffs_delta": round(cliffs_delta([m3[k] for k in common],
                                                                   [m2[k] for k in common]), 3)}
    false_fix_faults = collections.Counter(t["entry"] for t in c2 if t["outcome"] == "false_fix")
    out["c2_false_fix_faults"] = dict(false_fix_faults)
    main_rows = [t for t in rows(os.path.join(MAIN, "trials.jsonl")) if not t.get("variant")]
    out["opus_main_study"] = {a: summary([t for t in main_rows if t["arm"] == a]) for a in ("C2", "C3")}
    with open(os.path.join(HAIKU, "analysis.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    show = {k: v for k, v in out.items() if k != "opus_main_study"}
    print(json.dumps(show, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
