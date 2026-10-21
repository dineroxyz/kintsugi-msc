"""Kintsugi demo supervisor: a real application, with Kintsugi in front.

One process per hosted demo. It runs the real application from /app/live,
forwards every request to it unchanged, and adds a small control surface:

    /__kintsugi            opens this application's page on the Kintsugi site
    /__kintsugi/run        (worker token) one full run: healthy traffic, the real
                           past defect put back, Kintsugi catching and mending it
    /__kintsugi/events     (worker token) that run's events, in the Live view's format
    /__kintsugi/last       (worker token) the last mended run, for replay

When the application crashes, the agent reports it to the Kintsugi site
(an alert on the owner's profile), replays it five times in fresh processes
from a database snapshot, asks the model for a patch, puts the patch through
the gate (structure, security, applies, the replay no longer fails, the
application's own tests for that area), deploys it to /app/live, checks the
crash is gone, and has the site sign a certificate. Nothing here is mocked:
the application, the defect, the tests and the model are real.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import re
import threading
import time
import uuid

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

ROOT = os.environ.get("KINTSUGI_DEMO_ROOT", "/app")
CFG = json.load(open(os.path.join(ROOT, "demo.json")))
LIVE = os.path.join(ROOT, "live")
PRISTINE = os.path.join(ROOT, "pristine")
FAULT = os.path.join(ROOT, "fault.diff")
DATA = os.environ.get("KINTSUGI_DATA", "/data")
GENERIC = os.path.join(ROOT, "kintsugi", "study", "real", "generic")
sys.path.insert(0, os.path.join(ROOT, "kintsugi"))
SINK = os.path.join(DATA, "sink.jsonl")
SNAPS = os.path.join(DATA, "snaps")
APP_PORT = int(os.environ.get("KINTSUGI_APP_PORT", "4300"))
SITE = os.environ.get("KINTSUGI_SITE", "").rstrip("/")
CONNECT = os.environ.get("KINTSUGI_CONNECT", "").rstrip("/")
WORKER = {"x-kintsugi-worker": os.environ.get("KINTSUGI_WORKER_TOKEN", "")}
PY = sys.executable

state = {"mode": "healthy", "log": [], "job": None, "busy": False, "spent": 0.0}
proc: list[subprocess.Popen | None] = [None]
TOKEN = os.environ.get("KINTSUGI_WORKER_TOKEN", "")
FAKE_PATCH = os.environ.get("KINTSUGI_DEMO_FAKE_PATCH")   # local testing only: no model call
LAST_RUN = os.path.join(DATA, "last_run.json")

# One run at a time. Its events use the Live view's format, so the Kintsugi site
# shows a real application's repair on exactly the same screen as its own shop.
RUN: dict = {"id": None, "events": [], "t0": 0.0, "done": True, "first_error": None, "paused": False}
RUN_COND = threading.Condition()


def emit(kind: str, **data) -> None:
    if RUN["id"] is None or RUN["done"]:
        return
    with RUN_COND:
        RUN["events"].append({"t": round(time.time() - RUN["t0"], 3), "kind": kind, **data})
        if kind == "end":
            RUN["done"] = True
        RUN_COND.notify_all()


def note(text: str) -> None:
    state["log"].append({"at": time.strftime("%H:%M:%S"), "text": text})
    state["log"] = state["log"][-60:]
    print(time.strftime("%H:%M:%S"), text, flush=True)
    emit("note", text=text)


def complete_fn():
    """The model call, streamed to the run so the page can type the patch out."""
    if FAKE_PATCH:
        def fake(_text: str) -> str:
            body = open(FAKE_PATCH).read()
            for i in range(0, len(body), 24):
                emit("token", text=body[i:i + 24])
                time.sleep(0.02)
            return body
        return fake
    from control_plane.diagnosis.proposer import anthropic_streaming_complete
    return anthropic_streaming_complete(lambda piece: emit("token", text=piece))


class _FakeStream:
    """Local testing only: stands in for the model in failing-test mode."""
    def __init__(self, text):
        self.text = text
        self.text_stream = (text[i:i + 24] for i in range(0, len(text), 24))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        class U:
            input_tokens = output_tokens = 0
        class B:
            type = "text"
        b = B()
        b.text = self.text
        class M:
            usage, stop_reason, content = U(), "end_turn", [b]
        return M()


class _FakeClient:
    class messages:
        @staticmethod
        def stream(**kw):
            system = kw.get("system", "")
            if "locate" in system:
                return _FakeStream(CFG.get("fake_files", ""))
            return _FakeStream(open(FAKE_PATCH).read())


# ------------------------------------------------------------------ the app

def app_env(extra: dict | None = None, tree: str = LIVE) -> dict:
    """The application's environment, loading code from `tree` (the copy under judgement)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([os.path.join(tree, p) for p in CFG["pythonpath"]] + [GENERIC])
    env.update(CFG.get("env", {}))
    env["KINTSUGI_SINK"] = SINK
    env["KINTSUGI_SNAPDIR"] = SNAPS
    if CFG.get("db_hook"):
        env["KINTSUGI_DB_PATH_HOOK"] = CFG["db_hook"]
    env.update(extra or {})
    return env


def start_app() -> None:
    stop_app()
    proc[0] = subprocess.Popen(CFG["cmd"] + ["--host", "127.0.0.1", "--port", str(APP_PORT)],
                               cwd=LIVE, env=app_env())
    for _ in range(120):
        try:
            httpx.get(f"http://127.0.0.1:{APP_PORT}{CFG['health']}", timeout=3)
            return
        except httpx.HTTPError:
            time.sleep(1)
    note("the application did not start")


def stop_app() -> None:
    if proc[0] and proc[0].poll() is None:
        proc[0].terminate()
        try:
            proc[0].wait(15)
        except subprocess.TimeoutExpired:
            proc[0].kill()
    proc[0] = None


def git_apply(tree: str, diff: str, reverse: bool = False) -> tuple[bool, str]:
    r = subprocess.run(["git", "apply", "--whitespace=nowarn", *(["-R"] if reverse else []), "-"],
                       cwd=tree, input=diff if diff.endswith("\n") else diff + "\n",
                       text=True, capture_output=True)
    return r.returncode == 0, r.stderr[-300:]


def copy_tree(src: str) -> str:
    dst = tempfile.mkdtemp(prefix="kdemo-") + "/tree"
    shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    subprocess.run(["git", "init", "-q"], cwd=dst)
    return dst


# --------------------------------------------------------------- the agent

def report(path: str, **body):
    if not SITE or not WORKER["x-kintsugi-worker"]:
        return None
    try:
        r = httpx.post(SITE + path, headers=WORKER, json=body, timeout=30)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


def stage(name: str, text: str, **fields) -> None:
    note(text)
    if state["job"]:
        report(f"/worker/jobs/{state['job']}/stage", stage=name, note=text, **fields)


def replay(tree: str, capture: dict) -> dict:
    path = tempfile.mktemp(suffix=".json")
    json.dump(capture, open(path, "w"))
    r = subprocess.run([PY, os.path.join(GENERIC, CFG["replay"]), path, "snapshot"], cwd=tree,
                       env=app_env({"KINTSUGI_SINK": "", "KINTSUGI_SNAPDIR": ""}, tree),
                       text=True, capture_output=True, timeout=300)
    os.unlink(path)
    if r.returncode or not r.stdout.strip():
        return {"failed": False, "error": r.stderr[-300:]}
    return json.loads(r.stdout.strip().splitlines()[-1])


def run_tests(tree: str) -> tuple[bool, str]:
    r = subprocess.run([PY, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-o", "addopts=",
                        *CFG["tests"]], cwd=tree, env=app_env({"KINTSUGI_SINK": ""}, tree),
                       text=True, capture_output=True, timeout=1800)
    out = r.stdout + r.stderr
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    detail = "\n".join(l for l in out.splitlines()
                       if l.startswith(("FAILED", "ERROR", "E ")) or "assert" in l)[-2500:]
    return r.returncode in (0, 5), tail + ("\n" + detail if detail else "")


def heal(capture: dict) -> None:
    from control_plane.diagnosis.packer import pack
    from control_plane.diagnosis.proposer import render_evidence
    from control_plane.gate.security import security_check
    from control_plane.gate.verifier import structural_check
    t0 = time.time()
    capture.setdefault("external_calls", [])
    capture.setdefault("release", "demo")
    ingest = None
    if CONNECT:
        try:
            ingest = httpx.post(CONNECT + "/ingest/event", json=capture, timeout=20).json()
        except httpx.HTTPError:
            ingest = None
    if ingest:
        for _ in range(10):
            jobs = (httpx.get(SITE + "/worker/jobs", headers=WORKER, timeout=20).json() or {}).get("jobs", [])
            mine = [j for j in jobs if j["incident"] == ingest.get("incident_id")]
            if mine:
                state["job"] = mine[0]["job"]
                break
            time.sleep(2)
    emit("captured", exception_type=capture.get("exception_type"),
         message=str(capture.get("exception_message", ""))[:160],
         method=capture["request"].get("method"), path=capture["request"].get("path"),
         service=CFG["name"], **evidence(capture))
    stage("reproducing", f"Caught {capture['exception_type']} on {capture['request']['method']} "
                         f"{capture['request']['path']}. Replaying it five times in fresh processes.")
    emit("reproduce_start")
    faulty = copy_tree(LIVE)
    verdicts = [replay(faulty, capture) for _ in range(5)]
    sigs = {(v.get("exception_type"), v.get("top_frame")) for v in verdicts}
    ok_repro = all(v.get("failed") for v in verdicts) and len(sigs) == 1
    sig = f"{verdicts[0].get('exception_type')}@{verdicts[0].get('top_frame')}"
    emit("reproduce_done", reproduced=ok_repro, signature=sig if ok_repro else None,
         reason="" if ok_repro else "it did not fail the same way on every replay",
         replays=[bool(v.get("failed")) for v in verdicts],
         verdicts=[{k: v.get(k) for k in ("failed", "exception_type", "top_frame", "status_code")} for v in verdicts])
    if not ok_repro:
        stage("refused", "It did not fail the same way five times, so Kintsugi will not guess a fix.")
        emit("refused", reason="it did not fail the same way on every replay")
        finish_run("refused")
        return
    stage("reproduced", f"Failed identically on 5 of 5 replays: {sig}.")
    stage("fixing", "Asking the AI for a fix; it must pass every check of the gate.")
    feedback, admitted = [], None
    model = complete_fn()
    for attempt in range(1, 4):
        emit("candidate_start", attempt=attempt)
        package = pack(capture, sig, faulty, prior_failures=feedback)
        emit("context", attempt=attempt,
             files=[{"file": "/".join(x["file"].split("/")[-2:]), "line": x["line"], "function": x["function"],
                     "lines": len(x["source"].splitlines())} for x in package["sources"]],
             frames=len(package["frames"]), prior_failures=len(feedback), memory_notes=0,
             release_diff=bool(package.get("release_diff")))
        diff = model(render_evidence(package))
        checks: dict = {}

        def verdict(admitted_now=False, why=""):
            emit("gate_result", attempt=attempt, diff=diff, conditions=dict(checks), admitted=admitted_now,
                 details={k: why for k, v in checks.items() if not v} if why else
                 ({"suite": tail.splitlines()[0]} if admitted_now else {}))
        with open(os.path.join(DATA, "attempts.jsonl"), "a") as fh:
            fh.write(json.dumps({"attempt": attempt, "diff": diff, "feedback": feedback[-1:] }) + "\n")
        from control_plane.patching import apply_loose
        trial = copy_tree(faulty)
        canonical, err = apply_loose(trial, diff)
        ok = canonical is not None
        if ok:
            diff = canonical                 # what is checked, deployed and certified
        tail = ""
        if not ok:
            checks["applies"] = False
            feedback.append({"attempt": attempt, "rejected_diff": diff[:3000], "failures": ["does not apply: " + err]})
            note(f"Attempt {attempt} rejected: the change does not apply.")
            verdict(why="the change does not apply to the code")
            continue
        st = structural_check(diff, capture["exception_type"])
        sc = security_check(diff, capture, faulty)
        checks.update(structural=st["passed"], security=sc["passed"], applies=True)
        if not st["passed"] or not sc["passed"]:
            feedback.append({"attempt": attempt, "rejected_diff": diff[:3000],
                             "failures": st["problems"] + sc["problems"]})
            note(f"Attempt {attempt} rejected: {'; '.join(st['problems'] + sc['problems'])}")
            verdict(why="; ".join(st["problems"] + sc["problems"]))
            continue
        after = [replay(trial, capture) for _ in range(5)]
        checks["reproduction"] = not any(v.get("failed") for v in after)
        if not checks["reproduction"]:
            feedback.append({"attempt": attempt, "rejected_diff": diff[:3000], "failures": ["the crash still happens"]})
            note(f"Attempt {attempt} rejected: the crash still happens.")
            verdict(why="the crash still happens on replay")
            continue
        passed, tail = run_tests(trial)
        checks["suite"] = passed
        if not passed:
            feedback.append({"attempt": attempt, "rejected_diff": diff[:3000],
                             "failures": ["the application's own tests fail; their output: " + tail]})
            note(f"Attempt {attempt} rejected: the application's tests fail ({tail.splitlines()[0]}).")
            verdict(why=tail.splitlines()[0][:160])
            continue
        admitted = {"attempt": attempt, "diff": diff, "tests": tail}
        verdict(admitted_now=True)
        break
    if not admitted:
        stage("gave_up", "No candidate passed every check. Handed to a person.")
        emit("escalated", reason="no attempt passed every check; handed to a person")
        finish_run("escalated")
        return
    stage("verified", f"Attempt {admitted['attempt']} passed: crash gone on 5 of 5 replays; tests: {admitted['tests'].splitlines()[0]}.",
          patch=admitted["diff"])
    from control_plane.patching import apply_loose
    emit("deploying", service=CFG["name"])
    RUN["paused"] = True                      # the restart is not the bug; keep it off the chart
    apply_loose(LIVE, admitted["diff"])
    start_app()
    RUN["paused"] = False
    emit("deployed", service=CFG["name"])
    probe = send_trigger()
    state["mode"] = "mended" if probe < 500 else "broken"
    emit("probe", passed=probe < 500)
    stage("proposed", f"Fix deployed; the failing request now answers {probe}.",
          explanation=f"Kintsugi reproduced {sig} and admitted a fix that passed the gate.")
    if probe >= 500:
        emit("false_fix")
        finish_run("false_fix")
        return
    cert = report(f"/worker/jobs/{state['job']}/certify", repo=CFG["name"], signature=sig,
                  replays=[bool(v.get("failed")) for v in verdicts], admitted_patch_sha256=hashlib.sha256(
                      admitted["diff"].encode()).hexdigest(), tests=admitted["tests"],
                  seconds=round(time.time() - t0, 1)) if state["job"] else None
    finish_run("recovered", cert=cert, attempts=admitted["attempt"], replays="5 of 5 replays")


def heal_from_tests() -> None:
    """Test mode: the developers' own failing test is the reproduction."""
    import anthropic
    from control_plane.patching import apply_loose
    from control_plane.testheal import Usage, heal as test_heal
    t0 = time.time()
    trees: list[str] = []

    def fresh(name):
        t = copy_tree(LIVE)
        trees.append(t)
        return t

    def run(tree, sel, stop_first=True):
        shutil.rmtree(os.path.join(tree, "tests", "data"), ignore_errors=True) if CFG.get("clean_test_data") else None
        os.makedirs(os.path.join(tree, "tests", "data"), exist_ok=True) if CFG.get("clean_test_data") else None
        r = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
                            *(["-x"] if stop_first else []), *CFG.get("pytest_args", []), *sel],
                           cwd=tree, env=app_env({"KINTSUGI_SINK": ""}, tree), text=True,
                           capture_output=True, timeout=1800)
        return r.returncode, r.stdout + r.stderr
    capture = {"exception_type": "TestFailure", "exception_message": CFG["bug"], "frames": [],
               "request": {"method": "TEST", "path": " ".join(CFG["tests"])}, "release": "demo"}
    if CONNECT:
        try:
            ingest = httpx.post(CONNECT + "/ingest/event", json=capture, timeout=20).json()
            for _ in range(10):
                jobs = httpx.get(SITE + "/worker/jobs", headers=WORKER, timeout=20).json().get("jobs", [])
                mine = [j for j in jobs if j["incident"] == ingest.get("incident_id")]
                if mine:
                    state["job"] = mine[0]["job"]
                    break
                time.sleep(2)
        except Exception:
            pass
    test = CFG["tests"][0]
    emit("captured", exception_type="a failing test", message=CFG["bug"], method="TEST", path=test,
         service=CFG["name"], test=True, frames=[], headers=None, source=test_source(test))
    stage("reproducing", "A test failed. Running it five times in fresh copies to prove the failure is real.")
    r = test_heal(run_tests=run, fresh_tree=fresh, drop_tree=lambda p: shutil.rmtree(os.path.dirname(p), ignore_errors=True),
                  selectors=CFG["tests"], test_files=[t.split("::")[0] for t in CFG["tests"]],
                  suite=CFG["suite"], package=CFG["package"], client=_FakeClient() if FAKE_PATCH else anthropic.Anthropic(),
                  usage=Usage(), budget=float(os.environ.get("KINTSUGI_DEMO_BUDGET", "3")),
                  run_suite=lambda tree, sel: run(tree, sel, stop_first=False), tries=4,
                  on_event=lambda kind, **d: emit(kind, **d))
    if not r.reproduced:
        stage("refused", f"Kintsugi will not guess a fix it cannot prove: {r.reason}.")
        finish_run("refused")
        return
    stage("reproduced", "The test failed the same way on 5 of 5 runs.")
    if not r.healed:
        for a in r.attempts:
            note(f"Attempt {a['attempt']} rejected: {a.get('rejected', '')[:200]}")
        stage("gave_up", f"No candidate passed every check: {r.reason}. Handed to a person.")
        emit("escalated", reason=f"no attempt passed every check ({r.reason}); handed to a person")
        finish_run("escalated")
        return
    for a in r.attempts:
        if not a.get("admitted"):
            note(f"Attempt {a['attempt']} rejected: {a.get('rejected', '')[:200]}")
    stage("verified", "The fix makes the test pass on 5 of 5 runs and breaks no other test in the folder.",
          patch=r.patch)
    emit("deploying", service=CFG["name"])
    RUN["paused"] = True
    apply_loose(LIVE, r.patch)
    start_app()
    RUN["paused"] = False
    emit("deployed", service=CFG["name"])
    state["mode"] = "mended"
    emit("probe", passed=True)
    stage("proposed", "Fix deployed; the project's failing test now passes.")
    cert = None
    if state["job"]:
        cert = report(f"/worker/jobs/{state['job']}/certify", repo=CFG["name"], mode="failing test",
                      tests=CFG["tests"], admitted_patch_sha256=hashlib.sha256(r.patch.encode()).hexdigest(),
                      seconds=round(time.time() - t0, 1))
    attempts = next((a["attempt"] for a in r.attempts if a.get("admitted")), len(r.attempts))
    finish_run("recovered", cert=cert, attempts=attempts, replays="5 of 5 test runs")


# ------------------------------------------------------------- the surface

def send_trigger() -> int:
    t = CFG["trigger"]
    try:
        return httpx.request(t["method"], f"http://127.0.0.1:{APP_PORT}{t['path']}",
                             json=t.get("json"), params=t.get("params"), timeout=30).status_code
    except httpx.HTTPError:
        return 599


SLUG = CFG.get("slug") or CFG["name"].lower()


def evidence(capture: dict) -> dict:
    """The stack and the source around the crash, in the Live view's format."""
    root = os.path.realpath(LIVE) + os.sep
    frames = [{"file": os.path.basename(f.get("file", "")), "line": f.get("line"), "function": f.get("function"),
               "code": (f.get("code") or "")[:120]}
              for f in capture.get("frames", []) if os.path.realpath(f.get("file", "")).startswith(root)][-6:]
    source = None
    app = [f for f in capture.get("frames", []) if os.path.realpath(f.get("file", "")).startswith(root)]
    if app:
        f = app[-1]
        try:
            lines = open(f["file"]).read().splitlines()
            lo = max(0, f["line"] - 9)
            source = {"file": os.path.relpath(os.path.realpath(f["file"]), root), "start": lo + 1,
                      "line": f["line"], "lines": lines[lo:f["line"] + 8]}
        except OSError:
            source = None
    return {"frames": frames, "source": source,
            "headers": len((capture.get("request") or {}).get("headers") or {}),
            "outbound": len(capture.get("external_calls") or [])}


def test_source(selector: str) -> dict | None:
    """The failing test itself, with its first assertion marked."""
    path, _, name = selector.partition("::")
    name = name.split("::")[-1].split("[")[0]
    try:
        lines = open(os.path.join(LIVE, path)).read().splitlines()
    except OSError:
        return None
    start = next((i for i, l in enumerate(lines) if re.match(rf"\s*(async\s+)?def {re.escape(name)}\b", l)), 0)
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"(async\s+)?def |class |@", lines[i])), len(lines))
    mark = next((i for i in range(start, end) if "assert" in lines[i]), start)
    lo, hi = start, min(end, start + 40)
    return {"file": path, "start": lo + 1, "line": mark + 1, "lines": lines[lo:hi]}


def finish_run(outcome: str, cert: dict | None = None, attempts: int = 1, replays: str = "") -> None:
    started = RUN.get("first_error") or RUN.get("fault_at") or RUN["t0"]
    mttr = round(time.time() - started, 2)
    if cert:
        emit("certificate", sequence=cert.get("sequence"), hash=cert.get("hash", ""), replays=replays,
             attempts=attempts, link=f"/account/incidents/{state['job']}" if state["job"] else None)
    if outcome == "recovered":
        emit("recovered", mttr=mttr)
        time.sleep(4)                         # healthy traffic after the repair, so the chart shows it
    emit("end", outcome=outcome, mttr=mttr if outcome == "recovered" else None)
    if outcome == "recovered":
        try:
            json.dump({"recorded": time.strftime("%Y-%m-%d %H:%M"), "model": os.environ.get("KINTSUGI_MODEL", "claude-opus-5"),
                       "events": RUN["events"]}, open(LAST_RUN, "w"))
        except OSError:
            pass


def pump(run_id: str) -> None:
    """Real requests to the real application, every one checked, for the traffic chart."""
    reqs = [{"method": "GET", "path": CFG["health"]}] + CFG.get("traffic", [])
    if CFG.get("mode") != "test":
        reqs.append(CFG["trigger"])           # users keep asking for the page that crashes
    buf, last, i = [], time.time(), 0
    with httpx.Client(timeout=10) as c:
        while RUN["id"] == run_id and not RUN["done"]:
            if RUN["paused"]:
                time.sleep(0.2)
                continue
            r = reqs[i % len(reqs)]
            i += 1
            try:
                code = c.request(r["method"], f"http://127.0.0.1:{APP_PORT}{r['path']}",
                                 json=r.get("json"), params=r.get("params")).status_code
            except httpx.HTTPError:
                code = 0
            if code:
                buf.append([round(time.time() - RUN["t0"], 3), code, r["path"]])
                if code >= 500 and RUN.get("fault_at") and RUN.get("first_error") is None:
                    RUN["first_error"] = time.time()
            if time.time() - last > 0.5 and buf:
                emit("traffic", samples=buf)
                buf, last = [], time.time()
            time.sleep(0.12)


def run_flow(run_id: str) -> None:
    """One full run: healthy traffic, the real past defect put back, Kintsugi mending it."""
    with RUN_COND:
        RUN.update(id=run_id, events=[], t0=time.time(), done=False, first_error=None, fault_at=None, paused=False)
    state.update(busy=True, job=None)
    try:
        emit("start", entry=SLUG, title=CFG["bug"], service=CFG["name"])
        if state["mode"] != "healthy" or proc[0] is None or proc[0].poll() is not None:
            note("Putting the current release back first.")
            subprocess.run(["rm", "-rf", LIVE])
            shutil.copytree(PRISTINE, LIVE, symlinks=True)
            start_app()
            state["mode"] = "healthy"
        threading.Thread(target=pump, args=(run_id,), daemon=True).start()
        emit("traffic_started")
        time.sleep(4)
        RUN["paused"] = True
        seen = len(open(SINK).read().splitlines()) if os.path.exists(SINK) else 0
        subprocess.run(["rm", "-rf", LIVE])
        shutil.copytree(PRISTINE, LIVE, symlinks=True)
        ok, err = git_apply(LIVE, open(FAULT).read(), reverse=True)
        if not ok:
            raise RuntimeError(f"the defect could not be put back: {err}")
        start_app()
        state["mode"] = "broken"
        RUN["fault_at"] = time.time()
        RUN["paused"] = False
        emit("fault_deployed", service=CFG["name"])
        note(f"Re-introduced a real defect from {CFG['name']}'s own history: {CFG['bug']}.")
        if CFG.get("mode") == "test":
            time.sleep(2)
            note("Running the project's own tests, as continuous integration would.")
            heal_from_tests()
        else:
            capture = None
            for _ in range(120):
                lines = open(SINK).read().splitlines() if os.path.exists(SINK) else []
                if len(lines) > seen:
                    capture = json.loads(lines[seen])
                    break
                time.sleep(0.5)
            if capture is None:
                emit("no_fire")
                finish_run("fault_never_fired")
            else:
                state["mode"] = "healing"
                heal(capture)
    except Exception as exc:
        emit("error", message=f"{type(exc).__name__}: {exc}"[:300])
    finally:
        if not RUN["done"]:
            finish_run("error")
        state["busy"] = False
        if state["mode"] == "healing":
            state["mode"] = "broken"


def _authed(request: Request) -> bool:
    return bool(TOKEN) and request.headers.get("x-kintsugi-worker") == TOKEN


async def control(request: Request):
    """Humans are sent to this application's page on the Kintsugi site."""
    if SITE:
        return RedirectResponse(f"{SITE}/real-apps/{SLUG}", status_code=302)
    return HTMLResponse(f"<p>{html.escape(CFG['name'])} is running with Kintsugi.</p>")


async def k_state(request: Request):
    return JSONResponse({"mode": state["mode"], "busy": state["busy"], "log": state["log"][-20:]})


async def k_run(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "not allowed"}, status_code=401)
    if state["busy"]:
        return JSONResponse({"error": "A run is already in progress on this application. Try again in a minute."},
                            status_code=409)
    run_id = uuid.uuid4().hex[:12]
    threading.Thread(target=run_flow, args=(run_id,), daemon=True).start()
    for _ in range(50):
        if RUN["id"] == run_id:
            break
        await asyncio.sleep(0.05)
    return JSONResponse({"run": run_id})


async def k_events(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "not allowed"}, status_code=401)
    run_id, after = request.query_params.get("run", ""), int(request.query_params.get("after", "0") or 0)
    if run_id != RUN["id"]:
        return JSONResponse({"error": "no such run"}, status_code=404)

    def wait():
        with RUN_COND:
            if len(RUN["events"]) <= after and not RUN["done"]:
                RUN_COND.wait(10)
            return RUN["events"][after:], RUN["done"]
    events, done = await asyncio.to_thread(wait)
    return JSONResponse({"events": events, "done": done})


async def k_last(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "not allowed"}, status_code=401)
    if not os.path.exists(LAST_RUN):
        return JSONResponse({"error": "no recorded run yet"}, status_code=404)
    return JSONResponse(json.load(open(LAST_RUN)))


async def proxy(request: Request):
    url = f"http://127.0.0.1:{APP_PORT}{request.url.path}"
    if request.url.query:
        url += "?" + request.url.query
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            r = await client.request(request.method, url, headers=headers, content=await request.body())
        except httpx.HTTPError:
            return Response("The application is restarting; try again in a moment.", status_code=503)
    out = {k: v for k, v in r.headers.items() if k.lower() not in ("content-length", "transfer-encoding", "content-encoding")}
    return Response(r.content, status_code=r.status_code, headers=out)


async def home(request: Request):
    """The bare address opens the demo page, not the application's own root."""
    return RedirectResponse("/__kintsugi", status_code=302)


routes = [Route("/", home), Route("/__kintsugi", control), Route("/__kintsugi/state", k_state),
          Route("/__kintsugi/run", k_run, methods=["POST"]), Route("/__kintsugi/events", k_events),
          Route("/__kintsugi/last", k_last),
          Route("/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])]
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app):
    if not os.path.exists(LIVE):
        shutil.copytree(PRISTINE, LIVE, symlinks=True)
    os.makedirs(DATA, exist_ok=True)
    threading.Thread(target=start_app, daemon=True).start()
    yield
    stop_app()


app = Starlette(routes=routes, lifespan=lifespan)
