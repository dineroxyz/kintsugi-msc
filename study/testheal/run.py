"""Study T runner (see PROTOCOL.md). Writes study/testheal/trials.jsonl."""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "study", "real"))
import run_multi as M  # noqa: E402
from control_plane.testheal import Usage, heal  # noqa: E402

SEED, PER_APP, BUDGET = 259074235, 15, 40.0 - 17.58   # harness checks and discarded trials spent 17.58 first
OUT = os.path.join(HERE, "trials.jsonl")
PACKAGE = {"mealie": "mealie/", "lnbits": "lnbits/", "prefect": "src/prefect/", "langflow": "src/"}


def population(app):
    real = os.path.join(ROOT, "study", "real")
    res = os.path.join(real, "results.jsonl") if app == "mealie" else os.path.join(real, "apps", f"{app}.results.jsonl")
    cands = os.path.join(real, "candidates.jsonl") if app == "mealie" else os.path.join(real, "apps", f"{app}.candidates.jsonl")
    valid = {json.loads(l)["sha"] for l in open(res)
             if json.loads(l).get("kind") in ("wrong output", "runtime failure")}
    cs = sorted((json.loads(l) for l in open(cands) if json.loads(l)["sha"] in valid), key=lambda c: c["sha"])
    frozen = os.path.join(HERE, "sample.json")
    if os.path.exists(frozen):                    # the sample committed before any trial
        want = set(json.load(open(frozen))[app])
        picked = [c for c in cs if c["sha"] in want]
    else:
        picked = sorted(random.Random(SEED).sample(cs, min(PER_APP, len(cs))), key=lambda c: c["sha"])
    return [dict(c, app=app) for c in picked]


def main():
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    import anthropic
    client = anthropic.Anthropic()
    usage = Usage()
    done = {}
    if os.path.exists(OUT):
        for l in open(OUT):
            r = json.loads(l)
            done[r["sha"]] = r
            usage.spent += r.get("cost", 0)
    queues = {app: population(app) for app in PACKAGE}
    order = [c for i in range(PER_APP) for app in PACKAGE for c in queues[app][i:i + 1]]   # round robin
    for c in order:
        if c["sha"] in done or (only and c["sha"][:8] not in only):
            continue
        app, sid = c["app"], c["sha"][:8]
        repo = os.path.join(M.REAL, app)
        if usage.spent >= BUDGET:
            row = {"app": app, "sha": c["sha"], "subject": c["subject"], "status": "not run (cap)"}
        else:
            def fresh_tree(name, c=c, app=app, repo=repo, sid=sid):
                t = M.tree(repo, f"T-{app}-{sid}-{name}")
                M.sh(["git", "apply", "-R", "--whitespace=nowarn", "-"], cwd=t, input=c["fault_diff"])
                return t

            def run_tests(tree, sel, app=app):
                r = M.run_tests(app, tree, sel, os.path.join(tempfile.mkdtemp(), "s.jsonl"))
                return r.returncode, r.stdout + r.stderr
            sel = M.selectors(c, os.path.join(M.REAL, app))
            dirs = sorted({os.path.dirname(f) for f in c["test_files"]
                           if os.path.exists(os.path.join(M.REAL, app, f))})
            before = usage.spent
            try:
                r = heal(run_tests=run_tests, fresh_tree=fresh_tree, drop_tree=lambda p, repo=repo: M.drop(repo, p),
                         selectors=sel, test_files=[f for f in c["test_files"] if f.endswith(".py")],
                         suite=dirs, package=PACKAGE[app], client=client, usage=usage, budget=BUDGET,
                         run_suite=lambda tree, sel, app=app: (lambda r: (r.returncode, r.stdout + r.stderr))(
                             M.run_tests(app, tree, sel, os.path.join(tempfile.mkdtemp(), "s.jsonl"), stop_first=False)))
                row = {"app": app, "sha": c["sha"], "subject": c["subject"], "status": "run",
                       "reproduced": r.reproduced, "healed": r.healed, "reason": r.reason,
                       "files": r.files, "attempts": r.attempts, "patch": r.patch,
                       "last_call": usage.last}
            except Exception as exc:
                row = {"app": app, "sha": c["sha"], "subject": c["subject"], "status": "harness error",
                       "error": repr(exc)[:400]}
            row["cost"] = round(usage.spent - before, 4)
        with open(OUT, "a") as fh:
            fh.write(json.dumps(row) + "\n")
        print(json.dumps({k: row.get(k) for k in ("app", "sha", "status", "reproduced", "healed", "reason", "cost")}),
              f"total=${usage.spent:.2f}", flush=True)


if __name__ == "__main__":
    main()
