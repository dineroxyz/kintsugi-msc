"""Study R amendment R2, steps 3-5, for the runtime failures found in step 2.

    .venv/bin/python study/real/run_stage2.py prefect
Writes study/real/apps/<app>.stage2.jsonl. No model is called.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
from control_plane.gate.verifier import structural_check  # noqa: E402
import run_multi as M  # noqa: E402
from run_real import cheat  # noqa: E402

RUNS = 5
REPLAY = {"prefect": "replay_prefect.py"}
DB_HOOK = {"prefect": "kintsugi_prefect:db_path"}


def capture_with_snapshot(app, c, faulty, want_sig, scratch):
    sink = os.path.join(scratch, "sink.jsonl")
    os.environ["KINTSUGI_SNAPDIR"] = os.path.join(scratch, "snaps")
    os.environ["KINTSUGI_DB_PATH_HOOK"] = DB_HOOK[app]
    try:
        M.run_tests(app, faulty, M.selectors(c, faulty), sink)
    finally:
        os.environ.pop("KINTSUGI_SNAPDIR", None)
        os.environ.pop("KINTSUGI_DB_PATH_HOOK", None)
    evs = [e for e in M.events(sink) if M.sig(e) == want_sig]
    return evs[0] if evs else None


def replay(app, tree, cap, mode):
    path = tempfile.mktemp(suffix=".json")
    json.dump(cap, open(path, "w"))
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["PATH"] = os.path.join(M.REAL, app, ".venv", "bin") + os.pathsep + env["PATH"]
    for f in M.APPS[app].get("copy", []):
        src = os.path.join(M.REAL, app, f)
        if os.path.exists(src) and not os.path.exists(os.path.join(tree, f)):
            shutil.copy(src, os.path.join(tree, f))
    r = M.sh([os.path.join(M.REAL, app, ".venv", "bin", "python"),
              os.path.join(M.PLUGIN_DIR, REPLAY[app]), path, mode], cwd=tree, env=env, timeout=600)
    os.unlink(path)
    if r.returncode or not r.stdout.strip():
        return {"failed": False, "harness_error": r.stderr[-600:]}
    return json.loads(r.stdout.strip().splitlines()[-1])


def reproduce(app, tree, cap, mode):
    vs = [replay(app, tree, cap, mode) for _ in range(RUNS)]
    fails = [v for v in vs if v.get("failed")]
    if any("harness_error" in v for v in vs):
        return {"verdict": "refused", "reason": "harness error",
                "detail": next(v["harness_error"] for v in vs if "harness_error" in v)[-300:]}
    if not fails:
        return {"verdict": "refused", "reason": f"did not recur (status {sorted({v['status_code'] for v in vs})})"}
    if len(fails) < RUNS:
        return {"verdict": "refused", "reason": f"failed on only {len(fails)}/{RUNS}"}
    if len({(v["exception_type"], v["top_frame"]) for v in fails}) > 1:
        return {"verdict": "refused", "reason": "signature varied"}
    if fails[0]["exception_type"] != cap["exception_type"]:
        return {"verdict": "refused", "reason": f"failed differently ({fails[0]['exception_type']})"}
    return {"verdict": "reproduced", "reason": f"{RUNS}/{RUNS} identical", "signature": f"{fails[0]['exception_type']}@{fails[0]['top_frame']}"}


def suite_args(c, t):
    """The test modules in the folders of the discriminating tests, minus the fix's new tests."""
    dirs = sorted({os.path.dirname(f) for f in c["test_files"] if os.path.exists(os.path.join(t, f))})
    args = dirs + (["-k", "not (" + " or ".join(c["tests_added"]) + ")"] if c["tests_added"] else [])
    return args, dirs


def gate(app, c, cap, diff, name):
    repo = os.path.join(M.REAL, app)
    out = {"structural": structural_check(diff, cap["exception_type"])}
    t = M.tree(repo, name)
    try:
        assert M.sh(["git", "apply", "-R", "--whitespace=nowarn", "-"], cwd=t, input=c["fault_diff"]).returncode == 0
        out["applies"] = M.sh(["git", "apply", "--whitespace=nowarn", "-"], cwd=t, input=diff).returncode == 0
        if not out["applies"]:
            return out
        r = reproduce(app, t, cap, "snapshot")
        out["reproduction"] = {"passed": r["verdict"] == "refused" and r["reason"].startswith("did not recur"),
                               "detail": r["reason"]}
        args, dirs = suite_args(c, t)
        sr = M.run_tests(app, t, args,
                         os.path.join(tempfile.mkdtemp(), "s.jsonl"))
        out["suite"] = {"passed": sr.returncode in (0, 5), "scope": dirs,
                        "summary": (sr.stdout.strip().splitlines() or [""])[-1][:200]}
        d = M.run_tests(app, t, M.selectors(c, t), os.path.join(tempfile.mkdtemp(), "d.jsonl"))
        out["discriminating_tests_pass"] = d.returncode == 0
        out["admitted"] = all(out[k]["passed"] for k in ("structural", "reproduction", "suite"))
    finally:
        M.drop(repo, t)
    return out


def main():
    app = sys.argv[1]
    cands = {json.loads(l)["sha"]: json.loads(l) for l in open(os.path.join(HERE, "apps", f"{app}.candidates.jsonl"))}
    crashes = [json.loads(l) for l in open(os.path.join(HERE, "apps", f"{app}.results.jsonl"))
               if json.loads(l).get("kind") == "runtime failure"]
    out = os.path.join(HERE, "apps", f"{app}.stage2.jsonl")
    repo = os.path.join(M.REAL, app)
    keep = os.path.join(HERE, "apps", "captures")
    os.makedirs(keep, exist_ok=True)
    for r in crashes:
        c, sid = cands[r["sha"]], r["sha"][:8]
        res = {"app": app, "sha": r["sha"], "subject": r["subject"], "signature": r["signature"]}
        scratch = tempfile.mkdtemp(prefix=f"ks2-{sid}-")
        faulty = M.tree(repo, f"{app}-{sid}-s2")
        try:
            M.sh(["git", "apply", "-R", "--whitespace=nowarn", "-"], cwd=faulty, input=c["fault_diff"])
            cap = capture_with_snapshot(app, c, faulty, r["signature"], scratch)
            if cap is None:
                res["step"] = "failure not captured again with snapshots on"
            else:
                if cap.get("snapshot") and os.path.exists(cap["snapshot"]):
                    shutil.copy(cap["snapshot"], os.path.join(keep, f"{app}-{sid}.db"))
                    cap["snapshot"] = os.path.join(keep, f"{app}-{sid}.db")
                json.dump(cap, open(os.path.join(keep, f"{app}-{sid}.json"), "w"), indent=1)
                res["reproduction"] = {m: reproduce(app, faulty, cap, m) for m in ("request", "snapshot")}
                if res["reproduction"]["snapshot"]["verdict"] == "reproduced" or \
                        res["reproduction"]["request"]["verdict"] == "reproduced":
                    res["real_fix"] = gate(app, c, cap, c["fault_diff"], f"{app}-{sid}-fix")
                    for kind in ("S1", "S2"):
                        d = cheat(faulty, cap, kind)
                        res[kind] = gate(app, c, cap, d, f"{app}-{sid}-{kind}") if d else {"generated": False}
        except Exception as exc:
            res["error"] = repr(exc)[:400]
        finally:
            M.drop(repo, faulty)
            shutil.rmtree(scratch, ignore_errors=True)
        print(json.dumps({k: res.get(k) for k in ("sha", "step", "error")} |
                         {"repro": {m: v["verdict"] for m, v in (res.get("reproduction") or {}).items()}}), flush=True)
        with open(out, "a") as fh:
            fh.write(json.dumps(res) + "\n")


if __name__ == "__main__":
    main()
