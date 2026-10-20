"""Study R, all subjects: share of real defects that are runtime failures.

    python study/real/analyze_multi.py  -> study/real/apps/analysis.json
Mealie comes from the original Study R run (results.jsonl); the others from
amendment R2 (apps/<app>.results.jsonl). Frigate, Open WebUI and the FastAPI
template are reported with the reason they contribute nothing.
"""
import collections
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = {"mealie": os.path.join(HERE, "results.jsonl")}
for app in ("lnbits", "prefect", "langflow"):
    SOURCES[app] = os.path.join(HERE, "apps", f"{app}.results.jsonl")
NOT_RUN = {"frigate": "its tests need Frigate's Docker image (fixed system paths such as /labelmap.txt)",
           "open-webui": "no fix commit came with tests", "full-stack-fastapi-template": "no fix commit came with tests"}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h), 3), round(min(1, c + h), 3))


def kind(r):
    return "harness error" if r.get("error") else (r.get("step") or r.get("kind"))


out = {"apps": {}, "not_run": NOT_RUN, "crashes": []}
tot_valid = tot_crash = 0
for app, path in SOURCES.items():
    if not os.path.exists(path):
        continue
    rows = [json.loads(l) for l in open(path)]
    by = collections.Counter(kind(r) for r in rows)
    crash = by.get("runtime failure", 0)
    valid = crash + by.get("wrong output", 0)
    tot_valid += valid
    tot_crash += crash
    out["apps"][app] = {"mined": len(rows), "by_step": dict(by), "valid": valid,
                        "runtime_failures": crash,
                        "share": round(crash / valid, 3) if valid else None,
                        "ci95": wilson(crash, valid)}
    for r in rows:
        if r.get("kind") == "runtime failure":
            out["crashes"].append({"app": app, "sha": r["sha"][:8], "subject": r["subject"],
                                   "signature": r.get("signature"), "request": r.get("request")})
out["total"] = {"valid": tot_valid, "runtime_failures": tot_crash,
                "share": round(tot_crash / tot_valid, 3) if tot_valid else None,
                "ci95": wilson(tot_crash, tot_valid)}
json.dump(out, open(os.path.join(HERE, "apps", "analysis.json"), "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k != "not_run"}, indent=1))
