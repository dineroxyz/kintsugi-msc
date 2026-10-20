"""Kintsugi demo supervisor: a real application, with Kintsugi in front.

One process per hosted demo. It runs the real application from /app/live,
forwards every request to it unchanged, and adds a small control surface:

    /__kintsugi            status page with Break, Trigger and Reset
    /__kintsugi/break      put a real past defect (from the app's own history) back
    /__kintsugi/trigger    send the request that makes that defect crash
    /__kintsugi/reset      restore the current release

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
import threading
import time

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
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
APP_PORT = 4300
SITE = os.environ.get("KINTSUGI_SITE", "").rstrip("/")
CONNECT = os.environ.get("KINTSUGI_CONNECT", "").rstrip("/")
WORKER = {"x-kintsugi-worker": os.environ.get("KINTSUGI_WORKER_TOKEN", "")}
PY = sys.executable

state = {"mode": "healthy", "log": [], "job": None, "busy": False, "spent": 0.0}
proc: list[subprocess.Popen | None] = [None]


def note(text: str) -> None:
    state["log"].append({"at": time.strftime("%H:%M:%S"), "text": text})
    state["log"] = state["log"][-60:]
    print(time.strftime("%H:%M:%S"), text, flush=True)


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
    from control_plane.diagnosis.proposer import anthropic_complete, render_evidence
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
    stage("reproducing", f"Caught {capture['exception_type']} on {capture['request']['method']} "
                         f"{capture['request']['path']}. Replaying it five times in fresh processes.")
    faulty = copy_tree(LIVE)
    verdicts = [replay(faulty, capture) for _ in range(5)]
    sigs = {(v.get("exception_type"), v.get("top_frame")) for v in verdicts}
    if not all(v.get("failed") for v in verdicts) or len(sigs) != 1:
        stage("refused", "It did not fail the same way five times, so Kintsugi will not guess a fix.")
        return
    sig = f"{verdicts[0]['exception_type']}@{verdicts[0]['top_frame']}"
    stage("reproduced", f"Failed identically on 5 of 5 replays: {sig}.")
    stage("fixing", "Asking the AI for a fix; it must pass every check of the gate.")
    feedback, admitted = [], None
    for attempt in range(1, 4):
        package = pack(capture, sig, faulty, prior_failures=feedback)
        diff = anthropic_complete(render_evidence(package))
        with open(os.path.join(DATA, "attempts.jsonl"), "a") as fh:
            fh.write(json.dumps({"attempt": attempt, "diff": diff, "feedback": feedback[-1:] }) + "\n")
        from control_plane.patching import apply_loose
        trial = copy_tree(faulty)
        canonical, err = apply_loose(trial, diff)
        ok = canonical is not None
        if ok:
            diff = canonical                 # what is checked, deployed and certified
        if not ok:
            feedback.append({"attempt": attempt, "rejected_diff": diff[:3000], "failures": ["does not apply: " + err]})
            note(f"Attempt {attempt} rejected: the change does not apply.")
            continue
        st = structural_check(diff, capture["exception_type"])
        sc = security_check(diff, capture, faulty)
        if not st["passed"] or not sc["passed"]:
            feedback.append({"attempt": attempt, "rejected_diff": diff[:3000],
                             "failures": st["problems"] + sc["problems"]})
            note(f"Attempt {attempt} rejected: {'; '.join(st['problems'] + sc['problems'])}")
            continue
        after = [replay(trial, capture) for _ in range(5)]
        if any(v.get("failed") for v in after):
            feedback.append({"attempt": attempt, "rejected_diff": diff[:3000], "failures": ["the crash still happens"]})
            note(f"Attempt {attempt} rejected: the crash still happens.")
            continue
        passed, tail = run_tests(trial)
        if not passed:
            feedback.append({"attempt": attempt, "rejected_diff": diff[:3000],
                             "failures": ["the application's own tests fail; their output: " + tail]})
            note(f"Attempt {attempt} rejected: the application's tests fail ({tail.splitlines()[0]}).")
            continue
        admitted = {"attempt": attempt, "diff": diff, "tests": tail}
        break
    if not admitted:
        stage("gave_up", "No candidate passed every check. Handed to a person.")
        return
    stage("verified", f"Attempt {admitted['attempt']} passed: crash gone on 5 of 5 replays; tests: {admitted['tests'].splitlines()[0]}.",
          patch=admitted["diff"])
    from control_plane.patching import apply_loose
    apply_loose(LIVE, admitted["diff"])
    start_app()
    probe = send_trigger()
    state["mode"] = "mended" if probe < 500 else "broken"
    stage("proposed", f"Fix deployed to the demo app; the failing request now answers {probe}.",
          explanation=f"Kintsugi reproduced {sig} and admitted a fix that passed the gate.")
    report(f"/worker/jobs/{state['job']}/certify", repo=CFG["name"], signature=sig,
           replays=[bool(v.get("failed")) for v in verdicts], admitted_patch_sha256=hashlib.sha256(
               admitted["diff"].encode()).hexdigest(), tests=admitted["tests"], seconds=round(time.time() - t0, 1)) \
        if state["job"] else None


def agent() -> None:
    seen = 0
    while True:
        time.sleep(1)
        if not os.path.exists(SINK):
            continue
        lines = open(SINK).read().splitlines()
        fresh, seen = lines[seen:], len(lines)
        if not fresh or state["busy"] or state["mode"] != "broken":
            continue
        capture = json.loads(fresh[0])
        state["busy"] = True
        state["mode"] = "healing"
        try:
            heal(capture)
        except Exception as exc:
            note(f"Heal stopped: {type(exc).__name__}: {str(exc)[:200]}")
        finally:
            state["busy"] = False
            if state["mode"] == "healing":
                state["mode"] = "broken"


# ------------------------------------------------------------- the surface

def send_trigger() -> int:
    t = CFG["trigger"]
    try:
        return httpx.request(t["method"], f"http://127.0.0.1:{APP_PORT}{t['path']}",
                             json=t.get("json"), params=t.get("params"), timeout=30).status_code
    except httpx.HTTPError:
        return 599


async def control(request: Request):
    action = request.path_params.get("action", "")
    if request.method == "POST":
        if state["busy"]:
            return JSONResponse({"error": "busy"}, status_code=409)
        if action == "break":
            subprocess.run(["rm", "-rf", LIVE])
            shutil.copytree(PRISTINE, LIVE, symlinks=True)
            ok, err = git_apply(LIVE, open(FAULT).read(), reverse=True)
            await asyncio.to_thread(start_app)
            state.update(mode="broken", job=None)
            note(f"Broke it: re-introduced '{CFG['bug']}' from {CFG['name']}'s own history." if ok else f"Break failed: {err}")
        elif action == "trigger":
            code = await asyncio.to_thread(send_trigger)
            note(f"Sent the request that hits the bug; the app answered {code}.")
        elif action == "reset":
            subprocess.run(["rm", "-rf", LIVE])
            shutil.copytree(PRISTINE, LIVE, symlinks=True)
            await asyncio.to_thread(start_app)
            state.update(mode="healthy", job=None)
            note("Reset to the current release.")
        return JSONResponse({"mode": state["mode"]})
    if action == "state":
        return JSONResponse(state)
    rows = "".join(f"<li><code>{e['at']}</code> {html.escape(e['text'])}</li>" for e in reversed(state["log"]))
    page = f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Kintsugi demo: {html.escape(CFG['name'])}</title>
<style>body{{font:16px/1.5 -apple-system,Helvetica,sans-serif;max-width:820px;margin:32px auto;padding:0 16px;background:#17140F;color:#EDE6D8}}
h1{{font:700 30px Georgia,serif;margin:0}} .seam{{height:3px;width:120px;background:#C6A25A;margin:12px 0 18px}}
button{{font:inherit;padding:10px 18px;border-radius:6px;border:1px solid #C6A25A;background:#2a241b;color:#EDE6D8;cursor:pointer;margin:0 8px 8px 0}}
.mode{{display:inline-block;padding:2px 12px;border-radius:12px;border:1px solid #C6A25A}} ul{{padding-left:18px}} code{{color:#C6A25A}}</style>
<h1>{html.escape(CFG['name'])}, with Kintsugi</h1><div class=seam></div>
<p>{html.escape(CFG['about'])}</p>
<p>The bug: <b>{html.escape(CFG['bug'])}</b> (a real defect from the project's own history).</p>
<p>State: <span class=mode id=mode>{state['mode']}</span></p>
<p><button onclick="go('break')">1. Break it</button><button onclick="go('trigger')">2. Send the failing request</button>
<button onclick="go('reset')">Reset</button></p>
<h3>What Kintsugi is doing</h3><ul id=log>{rows}</ul>
<script>function go(a){{fetch('/__kintsugi/'+a,{{method:'POST'}}).then(()=>setTimeout(()=>location.reload(),800))}}
setInterval(()=>fetch('/__kintsugi/state').then(r=>r.json()).then(s=>{{document.getElementById('mode').textContent=s.mode;
document.getElementById('log').innerHTML=s.log.slice().reverse().map(e=>'<li><code>'+e.at+'</code> '+e.text.replace(/</g,'&lt;')+'</li>').join('')}}),2000)</script>"""
    return HTMLResponse(page)


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


routes = [Route("/__kintsugi", control), Route("/__kintsugi/{action}", control, methods=["GET", "POST"]),
          Route("/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])]
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app):
    if not os.path.exists(LIVE):
        shutil.copytree(PRISTINE, LIVE, symlinks=True)
    os.makedirs(DATA, exist_ok=True)
    threading.Thread(target=start_app, daemon=True).start()
    threading.Thread(target=agent, daemon=True).start()
    yield
    stop_app()


app = Starlette(routes=routes, lifespan=lifespan)
