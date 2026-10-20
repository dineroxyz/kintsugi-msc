"""Study R, amendment R2: steps 1 and 2 on more real applications.

    .venv/bin/python study/real/run_multi.py <app> [--only SHA8,...]
Writes study/real/apps/<app>.results.jsonl (resumable). No model is called.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.expanduser("~/kintsugi-real")
WORK = os.path.join(REAL, "work-multi")
PLUGIN_DIR = os.path.join(HERE, "generic")

APPS = {
    "mealie": {"pythonpath": ["."], "args": []},
    "prefect": {"pythonpath": ["src"], "args": ["-p", "no:randomly"],
                "copy": ["src/prefect/_build_info.py", "src/prefect/_internal/analytics/_config.py"]},
    "lnbits": {"pythonpath": ["."], "args": ["-p", "kintsugi_lnbits_setup"], "mkdir": ["tests/data"],
               "env": {"LNBITS_DATA_FOLDER": "./tests/data", "LNBITS_BACKEND_WALLET_CLASS": "FakeWallet",
                       "DEBUG": "true"}},
    "langflow": {"pythonpath": ["src/backend/base", "src/lfx/src"], "args": [],
                 # lfx's tests run in lfx's own environment, from its folder
                 "roots": {"src/lfx/tests": {"cwd": "src/lfx", "venv": "src/lfx/.venv",
                                             "pythonpath": ["src/lfx/src"]}}},
    "frigate": {"pythonpath": ["."], "args": []},
}


def sh(cmd, cwd=None, env=None, timeout=1800, input=None):
    try:
        return subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True,
                              timeout=timeout, input=input)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 124, exc.stdout or "", "timeout")


def tree(repo: str, name: str) -> str:
    path = os.path.join(WORK, name)
    if os.path.exists(path):
        drop(repo, path)
    r = sh(["git", "-C", repo, "worktree", "add", "--detach", path, "HEAD"])
    assert r.returncode == 0, r.stderr
    return path


def drop(repo: str, path: str) -> None:
    sh(["git", "-C", repo, "worktree", "remove", "--force", path])
    shutil.rmtree(path, ignore_errors=True)


def test_root(path: str) -> str:
    parts = path.split("/")
    return "/".join(parts[:parts.index("tests") + 1]) if "tests" in parts else parts[0]


def _paths(sel: list[str]) -> list[str]:
    out, skip = [], False
    for x in sel:
        if skip:
            skip = False
            continue
        if x == "-k":
            skip = True
            continue
        if not x.startswith("-"):
            out.append(x)
    return out


def run_tests(app: str, t: str, sel: list[str], sink: str) -> subprocess.CompletedProcess:
    """Test paths under different test roots run in separate pytest calls,
    because each root's conftest has the same module name."""
    files = _paths(sel)
    rest = [x for x in sel if x not in files]
    roots = sorted({test_root(f) for f in files})
    if len(roots) <= 1:
        return _run_tests(app, t, sel, sink)
    runs = [_run_tests(app, t, [f for f in files if test_root(f) == r] + rest, sink) for r in roots]
    worst = max(runs, key=lambda r: (r.returncode not in (0, 5), r.returncode))
    ok = all(r.returncode in (0, 5) for r in runs) and any(r.returncode == 0 for r in runs)
    return subprocess.CompletedProcess(worst.args, 0 if ok else (worst.returncode or 1),
                                       "".join(r.stdout for r in runs), "".join(r.stderr for r in runs))


def _run_tests(app: str, t: str, sel: list[str], sink: str) -> subprocess.CompletedProcess:
    cfg = dict(APPS[app])
    files = _paths(sel)
    special = cfg.get("roots", {}).get(test_root(files[0])) if files else None
    if special:                              # a sub-project with its own environment
        cfg.update(pythonpath=special["pythonpath"])
        cwd = os.path.join(t, special["cwd"])
        sel = [os.path.relpath(x, special["cwd"]) if x in files else x for x in sel]
        venv = os.path.join(REAL, app, special["venv"])
    else:
        cwd, venv = t, os.path.join(REAL, app, ".venv")
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["PYTHONPATH"] = os.pathsep.join([os.path.join(t, p) for p in cfg["pythonpath"]] + [PLUGIN_DIR])
    env["KINTSUGI_SINK"] = sink
    env["PATH"] = os.path.join(venv, "bin") + os.pathsep + env.get("PATH", "")
    env.update(cfg.get("env", {}))
    for f in cfg.get("copy", []):          # files the package install generates
        src = os.path.join(REAL, app, f)
        if os.path.exists(src) and not os.path.exists(os.path.join(t, f)):
            shutil.copy(src, os.path.join(t, f))
    for d in cfg.get("mkdir", []):
        os.makedirs(os.path.join(t, d), exist_ok=True)
    py = os.path.join(venv, "bin", "python")
    return sh([py, "-m", "pytest", "-p", "kintsugi_generic", "-p", "no:cacheprovider",
               "-o", "addopts=", "-q", "-x", *cfg["args"], *sel], cwd=cwd, env=env, timeout=1200)


def selectors(c: dict, t: str) -> list[str]:
    files = [f for f in c["test_files"] if os.path.exists(os.path.join(t, f))]
    if not files:
        return []
    return files + (["-k", " or ".join(c["tests_added"])] if c["tests_added"] else [])


def events(path: str) -> list[dict]:
    return [json.loads(l) for l in open(path)] if os.path.exists(path) else []


def sig(e: dict) -> str:
    app = [f for f in e["frames"] if "/site-packages/" not in f["file"] and "/lib/python" not in f["file"]]
    f = (app or e["frames"] or [{"file": "?", "line": 0}])[-1]
    return f"{e['exception_type']}@{os.path.basename(f['file'])}:{f['line']}"


def study(app: str, c: dict) -> dict:
    repo = os.path.join(REAL, app)
    sid = c["sha"][:8]
    res = {"app": app, "sha": c["sha"], "subject": c["subject"], "date": c["date"]}
    clean, faulty = tree(repo, f"{app}-{sid}-clean"), tree(repo, f"{app}-{sid}-faulty")
    scratch = tempfile.mkdtemp(prefix=f"kr2-{sid}-")
    try:
        if sh(["git", "apply", "-R", "--whitespace=nowarn", "-"], cwd=faulty, input=c["fault_diff"]).returncode:
            res["step"] = "fault did not apply"
            return res
        sel = selectors(c, clean)
        if not sel:
            res["step"] = "no discriminating tests at HEAD"
            return res
        sc, sf = os.path.join(scratch, "c.jsonl"), os.path.join(scratch, "f.jsonl")
        rc, rf = run_tests(app, clean, sel, sc), run_tests(app, faulty, sel, sf)
        res["clean_rc"], res["faulty_rc"] = rc.returncode, rf.returncode
        res["valid"] = rc.returncode == 0 and rf.returncode not in (0, 5)
        if not res["valid"]:
            res["step"] = "invalid ground truth"
            res["clean_tail"], res["faulty_tail"] = rc.stdout[-300:] + rc.stderr[-200:], rf.stdout[-300:]
            return res
        base = {sig(e) for e in events(sc)}
        new = [e for e in events(sf) if sig(e) not in base]
        res["kind"] = "runtime failure" if new else "wrong output"
        if new:
            res["signature"] = sig(new[0])
            res["request"] = f"{new[0]['request']['method']} {new[0]['request']['path']}"
            res["capture"] = new[0]
        return res
    finally:
        drop(repo, clean)
        drop(repo, faulty)
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> None:
    app = sys.argv[1]
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    os.makedirs(WORK, exist_ok=True)
    out = os.path.join(HERE, "apps", f"{app}.results.jsonl" if not only else f"{app}.trial.jsonl")
    done = {json.loads(l)["sha"] for l in open(out)} if os.path.exists(out) and not only else set()
    for line in open(os.path.join(HERE, "apps", f"{app}.candidates.jsonl")):
        c = json.loads(line)
        if c["sha"] in done or (only and c["sha"][:8] not in only):
            continue
        try:
            res = study(app, c)
        except Exception as exc:
            res = {"app": app, "sha": c["sha"], "subject": c["subject"], "error": repr(exc)[:400]}
        print(json.dumps({k: res.get(k) for k in ("sha", "kind", "valid", "step", "error")}), flush=True)
        with open(out, "a") as fh:
            fh.write(json.dumps(res) + "\n")


if __name__ == "__main__":
    main()
