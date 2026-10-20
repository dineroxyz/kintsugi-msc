"""Study R runner (see PROTOCOL.md). No model is called.

    .venv/bin/python study/real/run_real.py [--only SHA8,...]
Writes study/real/results.jsonl, one line per candidate, resumable.
"""
from __future__ import annotations

import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from control_plane.gate.verifier import structural_check  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.expanduser("~/kintsugi-real/mealie")
PY = os.path.join(REPO, ".venv/bin/python")
WORK = os.path.expanduser("~/kintsugi-real/work")
INM = os.path.join(HERE, "inmealie")
OUT = os.path.join(HERE, "results.jsonl")
RUNS = 5


def sh(cmd, cwd=None, env=None, timeout=1800, input=None):
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True,
                          timeout=timeout, input=input)


def tree_at_head(name: str) -> str:
    path = os.path.join(WORK, name)
    if os.path.exists(path):
        sh(["git", "-C", REPO, "worktree", "remove", "--force", path])
        shutil.rmtree(path, ignore_errors=True)
    r = sh(["git", "-C", REPO, "worktree", "add", "--detach", path, "HEAD"])
    assert r.returncode == 0, r.stderr
    return path


def drop_tree(path: str) -> None:
    sh(["git", "-C", REPO, "worktree", "remove", "--force", path])
    shutil.rmtree(path, ignore_errors=True)


def apply(tree: str, diff: str, reverse=False) -> bool:
    args = ["git", "apply", "--whitespace=nowarn"] + (["-R"] if reverse else []) + ["-"]
    return sh(args, cwd=tree, input=diff).returncode == 0


def run_tests(tree: str, args: list[str], sink: str | None = None,
              snapdir: str | None = None, timeout=1800) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = INM
    env.pop("VIRTUAL_ENV", None)
    if sink:
        env["KINTSUGI_SINK"] = sink
    if snapdir:
        env["KINTSUGI_SNAPDIR"] = snapdir
    return sh([PY, "-m", "pytest", "-p", "kintsugi_probe", "-p", "no:cacheprovider",
               "-o", "addopts=", "-q", *args],
              cwd=tree, env=env, timeout=timeout)


def selectors(c: dict, tree: str) -> list[str]:
    files = [f for f in c["test_files"] if os.path.exists(os.path.join(tree, f))]
    if not files:
        return []
    if c["tests_added"]:
        return files + ["-k", " or ".join(c["tests_added"])]
    return files


def events(sink: str) -> list[dict]:
    if not os.path.exists(sink):
        return []
    return [json.loads(l) for l in open(sink) if l.strip()]


def sig(e: dict) -> str:
    app = [f for f in e["frames"] if "/site-packages/" not in f["file"]
           and "/lib/python" not in f["file"] and "/kintsugi/libs/" not in f["file"]]
    f = (app or e["frames"])[-1]
    return f"{e['exception_type']}@{os.path.basename(f['file'])}:{f['line']}"


def replay(tree: str, capture: dict, mode: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(capture, f)
        path = f.name
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    try:
        r = sh([PY, os.path.join(INM, "replay_mealie.py"), path, mode], cwd=tree,
               env=env, timeout=300)
    finally:
        os.unlink(path)
    if r.returncode != 0 or not r.stdout.strip():
        return {"failed": False, "harness_error": r.stderr[-1500:]}
    return json.loads(r.stdout.strip().splitlines()[-1])


def reproduce(tree: str, capture: dict, mode: str) -> dict:
    vs = [replay(tree, capture, mode) for _ in range(RUNS)]
    errs = [v for v in vs if "harness_error" in v]
    fails = [v for v in vs if v.get("failed")]
    want = capture["exception_type"]
    if errs:
        verdict, why = "refused", f"harness error on {len(errs)}/{RUNS} replays"
    elif not fails:
        codes = sorted({v["status_code"] for v in vs})
        verdict, why = "refused", f"captured failure did not recur (status {codes})"
    elif len(fails) < RUNS:
        verdict, why = "refused", f"failed on only {len(fails)}/{RUNS} replays"
    elif len({(v["exception_type"], v["top_frame"]) for v in fails}) > 1:
        verdict, why = "refused", "failure signature varied across replays"
    elif fails[0]["exception_type"] != want:
        verdict, why = "refused", (f"failed differently from the capture "
                                   f"({fails[0]['exception_type']} vs {want})")
    else:
        verdict, why = "reproduced", f"failed identically on {RUNS}/{RUNS} replays"
    return {"verdict": verdict, "reason": why, "replays": vs}


def suite_passes(tree: str, c: dict) -> dict:
    ks = c["tests_added"]
    args = ["tests/unit_tests", "tests/integration_tests"]
    if ks:
        args += ["-k", "not (" + " or ".join(ks) + ")"]
    r = run_tests(tree, args, timeout=3600)
    tail = r.stdout.strip().splitlines()[-1:] if r.stdout.strip() else []
    return {"passed": r.returncode in (0, 5), "summary": tail[0] if tail else r.stderr[-300:]}


def cheat(tree: str, e: dict, kind: str) -> str | None:
    """Wrap the function holding the deepest app frame in a handler."""
    app = [f for f in e["frames"] if f["file"].startswith(tree)]
    if not app:
        return None
    fr = app[-1]
    rel = os.path.relpath(fr["file"], tree)
    src = open(fr["file"]).read()
    mod = ast.parse(src)
    best = None
    for node in ast.walk(mod):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.lineno <= fr["line"] <= node.end_lineno:
            if best is None or node.lineno > best.lineno:
                best = node
    if best is None:
        return None
    lines = src.splitlines(keepends=True)
    first = best.body[0]
    if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
            and isinstance(first.value.value, str) and len(best.body) > 1:
        first = best.body[1]
    start, end = first.lineno - 1, best.end_lineno
    if getattr(first, "decorator_list", None):
        start = first.decorator_list[0].lineno - 1
    indent = re.match(r"\s*", lines[start]).group(0)
    body = ["    " + l if l.strip() else l for l in lines[start:end]]
    exc = e["exception_type"]
    module = e.get("exception_module") or "builtins"
    imp = [] if module == "builtins" else [f"{indent}from {module} import {exc}\n"]
    if kind == "S1":
        handler = [f"{indent}except {exc}:\n", f"{indent}    return None\n"]
    else:
        handler = [f"{indent}except {exc} as kintsugi_exc:\n",
                   f"{indent}    from fastapi import HTTPException as _KHTTPException\n",
                   f"{indent}    raise _KHTTPException(status_code=400, detail=str(kintsugi_exc))\n"]
    new = lines[:start] + imp + [f"{indent}try:\n"] + body + handler + lines[end:]
    if not new[-1].endswith("\n"):
        new[-1] += "\n"
    diff = difflib.unified_diff(lines, new, f"a/{rel}", f"b/{rel}")
    return "".join(diff)


def gate(tree_faulty: str, c: dict, capture: dict, diff: str, name: str) -> dict:
    out = {"structural": structural_check(diff, capture["exception_type"])}
    tree = tree_at_head(name)
    try:
        assert apply(tree, c["fault_diff"], reverse=True)
        out["applies"] = apply(tree, diff)
        if not out["applies"]:
            return out
        r = reproduce(tree, capture, "snapshot")
        out["reproduction"] = {"passed": r["verdict"] == "refused" and
                               "did not recur" in r["reason"], "detail": r["reason"]}
        out["suite"] = suite_passes(tree, c)
        sel = selectors(c, tree)
        if sel:
            d = run_tests(tree, sel)
            out["discriminating_tests_pass"] = d.returncode == 0
        out["admitted"] = all(out[k]["passed"] for k in ("structural", "reproduction", "suite"))
    finally:
        drop_tree(tree)
    return out


def study(c: dict) -> dict:
    sid = c["sha"][:8]
    res = {"sha": c["sha"], "subject": c["subject"], "date": c["date"]}
    clean = tree_at_head(f"{sid}-clean")
    faulty = tree_at_head(f"{sid}-faulty")
    scratch = tempfile.mkdtemp(prefix=f"kintsugi-r-{sid}-")
    try:
        assert apply(faulty, c["fault_diff"], reverse=True)
        sel = selectors(c, clean)
        if not sel:
            res["step"] = "no discriminating tests at HEAD"
            return res
        s_clean, s_faulty = os.path.join(scratch, "clean.jsonl"), os.path.join(scratch, "faulty.jsonl")
        rc = run_tests(clean, sel, sink=s_clean)
        rf = run_tests(faulty, sel, sink=s_faulty, snapdir=os.path.join(scratch, "snaps"))
        res["clean_pass"] = rc.returncode == 0
        res["faulty_fail"] = rf.returncode != 0
        res["valid"] = res["clean_pass"] and res["faulty_fail"]
        if not res["valid"]:
            res["step"] = "invalid ground truth"
            res["clean_tail"] = rc.stdout[-400:]
            res["faulty_tail"] = rf.stdout[-400:]
            return res
        base = {sig(e) for e in events(s_clean)}
        new = [e for e in events(s_faulty) if sig(e) not in base]
        if not new:
            res["kind"] = "wrong output"
            return res
        res["kind"] = "runtime failure"
        cap = new[0]
        keep = os.path.join(HERE, "captures", f"{sid}.json")
        os.makedirs(os.path.dirname(keep), exist_ok=True)
        if cap.get("snapshot") and os.path.exists(cap["snapshot"]):
            snap_keep = os.path.join(HERE, "captures", f"{sid}.db")
            shutil.copy(cap["snapshot"], snap_keep)
            cap["snapshot"] = snap_keep
        json.dump(cap, open(keep, "w"), indent=1)
        res["signature"] = sig(cap)
        res["request"] = f"{cap['request']['method']} {cap['request']['path']}"
        res["reproduction"] = {m: {k: v for k, v in reproduce(faulty, cap, m).items()
                                   if k != "replays"} for m in ("request", "snapshot")}
        if res["reproduction"]["snapshot"]["verdict"] != "reproduced":
            return res
        res["real_fix"] = gate(faulty, c, cap, c["fault_diff_forward"], f"{sid}-fix")
        for kind in ("S1", "S2"):
            d = cheat(faulty, cap, kind)
            res[kind] = gate(faulty, c, cap, d, f"{sid}-{kind}") if d else {"generated": False}
        return res
    finally:
        drop_tree(clean)
        drop_tree(faulty)
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> None:
    os.makedirs(WORK, exist_ok=True)
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    done = {json.loads(l)["sha"] for l in open(OUT)} if os.path.exists(OUT) else set()
    for line in open(os.path.join(HERE, "candidates.jsonl")):
        c = json.loads(line)
        if c["sha"] in done or (only and c["sha"][:8] not in only):
            continue
        # the fix as a forward patch against the faulty tree is the mined diff itself
        c["fault_diff_forward"] = c["fault_diff"]
        try:
            res = study(c)
        except Exception as exc:  # recorded, never silently skipped
            res = {"sha": c["sha"], "subject": c["subject"], "error": repr(exc)[:500]}
        print(json.dumps({k: res.get(k) for k in ("sha", "kind", "valid", "step", "error")}),
              flush=True)
        if not only:
            with open(OUT, "a") as f:
                f.write(json.dumps(res) + "\n")
        else:
            with open(os.path.join(HERE, "trial_only.jsonl"), "a") as f:
                f.write(json.dumps(res) + "\n")


if __name__ == "__main__":
    main()
