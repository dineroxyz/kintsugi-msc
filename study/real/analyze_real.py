"""Summarise Study R (study/real/results.jsonl) -> study/real/analysis.json."""
import collections
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(os.path.join(HERE, "results.jsonl"))]


def kind(r):
    if r.get("error"):
        return "harness error"
    if r.get("step"):
        return r["step"]
    return r.get("kind")


out = {"candidates": len(rows), "by_kind": dict(collections.Counter(kind(r) for r in rows))}
rt = [r for r in rows if r.get("kind") == "runtime failure"]
out["runtime_failures"] = []
for r in rt:
    item = {"sha": r["sha"][:8], "subject": r["subject"], "signature": r.get("signature"),
            "request": r.get("request"),
            "reproduction": {m: v["verdict"] + ": " + v["reason"]
                             for m, v in (r.get("reproduction") or {}).items()}}
    for k in ("real_fix", "S1", "S2"):
        g = r.get(k)
        if not g:
            continue
        if g.get("generated") is False:
            item[k] = "not generated"
            continue
        item[k] = {"structural": g["structural"]["passed"], "applies": g.get("applies"),
                   "reproduction": (g.get("reproduction") or {}).get("passed"),
                   "suite": (g.get("suite") or {}).get("passed"),
                   "admitted": g.get("admitted"),
                   "discriminating_tests_pass": g.get("discriminating_tests_pass")}
    out["runtime_failures"].append(item)
for mode in ("request", "snapshot"):
    out[f"reproduced_{mode}"] = sum(1 for r in rt if (r.get("reproduction") or {})
                                   .get(mode, {}).get("verdict") == "reproduced")
json.dump(out, open(os.path.join(HERE, "analysis.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
