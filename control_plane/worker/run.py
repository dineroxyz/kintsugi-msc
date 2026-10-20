"""The Kintsugi worker: runs next to the owner's code and heals their app.

    python -m control_plane.worker.run              poll the site and heal new incidents
    python -m control_plane.worker.run --once       handle what is waiting, then stop
    python -m control_plane.worker.run --drill dinero-img-guard --connect <connect url>
        fire drill: plant a known bug in a sealed copy of the app, send one
        real request so the crash reaches Kintsugi like any other, then heal it

Configuration: ~/.kintsugi/worker.env (KINTSUGI_SITE, KINTSUGI_WORKER_TOKEN);
the model key is read from the Kintsugi project's .env. Nothing here is ever
printed. Pull requests are opened only with KINTSUGI_OPEN_PRS=1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from control_plane.worker import film, fixer  # noqa: E402
from control_plane.worker.nextjs import NextSandbox  # noqa: E402

HOME = os.path.expanduser("~")
LIVE = os.path.join(HOME, "kintsugi-real", "live")
SANDBOXES = os.path.join(LIVE, "sandboxes")
DRILLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drills")
CAP_USD = float(os.environ.get("KINTSUGI_CAP_USD", "20"))
DRILL_REQUESTS = {
    "dinero-img-guard": {"repo": "dineroxyz/dinero-app", "method": "GET",
                         "path": "/api/img/7d0c8f9e-2b1a-4c3d-9e8f-0a1b2c3d4e5f"},
}


def load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(os.path.join(HOME, ".kintsugi", "worker.env"))
load_env(os.path.join(ROOT, ".env"))
SITE = os.environ.get("KINTSUGI_SITE", "").rstrip("/")
HEADERS = {"x-kintsugi-worker": os.environ.get("KINTSUGI_WORKER_TOKEN", "")}


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def api(method: str, path: str, **kw):
    r = httpx.request(method, SITE + path, headers={**HEADERS, **kw.pop("headers", {})},
                      timeout=120, **kw)
    r.raise_for_status()
    return r.json()


def stage(job: int, name: str, note: str = "", **fields) -> None:
    api("POST", f"/worker/jobs/{job}/stage", json={"stage": name, "note": note, **fields})
    log(f"[job {job}] {name}: {note}")


def clone_for(repo: str) -> str:
    """A local clone of the owner's repository, kept up to date."""
    path = os.path.join(LIVE, repo.split("/")[1])
    helper = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    if not os.path.exists(path):
        subprocess.run(["git", *helper, "clone", "-q", f"https://github.com/{repo}.git", path],
                       check=True, timeout=600)
    else:
        subprocess.run(["git", *helper, "-C", path, "fetch", "-q", "origin"], timeout=600)
    lock = os.path.join(path, "package-lock.json")
    stamp = os.path.join(path, "node_modules", ".kintsugi-lock")
    digest = hashlib.sha256(open(lock, "rb").read()).hexdigest() if os.path.exists(lock) else ""
    if not os.path.exists(stamp) or open(stamp).read() != digest:
        log(f"Installing {repo} packages once for the sealed copies.")
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=path, check=True,
                       capture_output=True, timeout=1800)
        open(stamp, "w").write(digest)
    return path


def resolve_release(clone: str, release: str | None, branch: str) -> tuple[str, str | None, bool]:
    """(commit, drill fault diff or None, is_drill)."""
    if release and release.startswith("drill:"):
        _, name, sha = release.split(":", 2)
        return sha, open(os.path.join(DRILLS, f"{name}.diff")).read(), True
    r = subprocess.run(["git", "-C", clone, "cat-file", "-e", f"{release}^{{commit}}"],
                       capture_output=True) if release and release != "unknown" else None
    if r is not None and r.returncode == 0:
        return release, None, False
    head = subprocess.run(["git", "-C", clone, "rev-parse", f"origin/{branch}"],
                          capture_output=True, text=True).stdout.strip()
    return head, None, False


def heal(job: dict, spent: float) -> None:
    jid, cap = job["job"], job["capture"]
    if not job.get("repo"):
        stage(jid, "refused", "Link this service's GitHub repository on your account so Kintsugi can reproduce it.")
        return
    if (job.get("framework") or "nextjs") != "nextjs":
        stage(jid, "refused", "This worker heals Next.js apps; Python services are healed by the main platform.")
        return
    t0 = time.time()
    stage(jid, "reproducing", "Starting a separate copy of the app (its own empty database, no live keys, "
                              "no outside network) and replaying the request.")
    clone = clone_for(job["repo"])
    sha, fault, drill = resolve_release(clone, cap.get("release"), job.get("branch") or "main")
    sb = NextSandbox(clone, sha, f"job-{jid}", SANDBOXES)
    evidence = {"job": jid, "repo": job["repo"], "release": sha, "drill": drill,
                "request": {k: cap["request"].get(k) for k in ("method", "path", "query")},
                "captured_error": {"type": cap["exception_type"], "message": cap.get("exception_message", "")[:300]},
                "sandbox": "git worktree at the release; empty Postgres with the app's schema; "
                           "fresh session secret; outbound network blocked"}
    sha_hint = (cap.get("release") or "").startswith("drill:")
    scenes = [(film.scene("1 · Detected", "A crash in a fire drill copy" if sha_hint else "A crash on the live app",
                          f"{cap['request']['method']} {cap['request']['path']} failed with "
                          f"<b>{cap['exception_type']}</b>: {cap.get('exception_message', '')[:160]}",
                          footer=job["repo"]), 4)]
    try:
        try:
            sb.prepare(fault)
            sb.start()
        except Exception as exc:
            reason = f"the separate copy of the app could not be started ({str(exc)[:200]})"
            evidence["reproduction"] = {"verdict": "refused", "reason": reason}
            stage(jid, "refused", f"Kintsugi will not guess a fix it cannot prove: {reason}.")
            scenes.append((film.scene("2 · Could not start a copy", "No proof, no fix", reason.capitalize()), 6))
            return
        repro = sb.reproduce(cap)
        evidence["reproduction"] = {"verdict": repro["verdict"], "reason": repro["reason"],
                                    "signature": repro.get("signature"),
                                    "replays": [{"failed": v.get("failed"), "status": v.get("status_code")}
                                                for v in repro["replays"]]}
        rows = "\n".join(f"replay {i + 1}: {'crashed' if v.get('failed') else 'no crash'} "
                         f"(HTTP {v.get('status_code')})" for i, v in enumerate(repro["replays"]))
        scenes.append((film.scene("2 · Reproduced in a separate copy" if repro["verdict"] == "reproduced"
                                  else "2 · Could not reproduce", repro["reason"].capitalize(),
                                  f"Signature: {repro.get('signature') or 'none'}", rows,
                                  footer="no live data, no live keys"), 5))
        if repro["verdict"] != "reproduced":
            stage(jid, "refused", f"Kintsugi will not guess a fix it cannot prove: {repro['reason']}.")
            scenes.append((film.scene("3 · Left for a person", "No proof, no fix",
                                      "Kintsugi only proposes fixes it can test against the real crash. "
                                      "This one goes to you with everything it recorded."), 5))
            return
        stage(jid, "reproduced", f"{repro['reason'].capitalize()}. Crash point: {repro['signature']}.")
        stage(jid, "fixing", "Asking the AI for a fix; every candidate must stop the crash, type-check and "
                             "pass the app's own tests.")
        result = fixer.fix(sb, cap, repro, max(0.0, CAP_USD - spent), lambda m: log(f"[job {jid}] {m}"))
        evidence["candidates"] = [{k: v for k, v in c.items() if k != "explanation"} for c in result["candidates"]]
        evidence["model"] = fixer.MODEL
        evidence["cost_usd"] = round(result["cost"], 4)
        if not result["admitted"]:
            stage(jid, "gave_up", f"No safe fix found: {result['reason']}.", cost_usd=result["cost"])
            scenes.append((film.scene("3 · No safe fix", "Every candidate failed a check",
                                      "<br>".join(f"Attempt {c['attempt']}: {c.get('rejected', '')[:120]}"
                                                  for c in result["candidates"])), 6))
            return
        cand = result["candidate"]
        evidence["admitted_patch_sha256"] = hashlib.sha256(cand["diff"].encode()).hexdigest()
        evidence["checks"] = {"structural": "passed", "replays_after": cand["replays_after"],
                              "typecheck": "passed", "tests": cand["tests"]["summary"]}
        stage(jid, "verified", f"Attempt {cand['attempt']} passed: the crash is gone on 5 of 5 replays, "
                               f"the code type-checks, tests: {cand['tests']['summary']}.",
              patch=cand["diff"], explanation=result["explanation"], cost_usd=result["cost"])
        scenes.append((film.scene("3 · The fix", "What the AI changed", result["explanation"],
                                  cand["diff"], footer=f"attempt {cand['attempt']} of {fixer.MAX_TRIES}"), 7))
        after = "\n".join(f"replay {i + 1}: no crash (HTTP {v['status']})" for i, v in enumerate(cand["replays_after"]))
        scenes.append((film.scene("4 · Tested", "The crash is gone, and nothing broke",
                                  f"Types: passed. App tests: {cand['tests']['summary']}.", after), 5))
        pr = open_pr(clone, job, sha, cand["diff"], result["explanation"]) if not drill else ""
        stage(jid, "proposed",
              ("A pull request is ready for you to review and merge." if pr else
               "Fire drill: the fix is attached here; nothing was sent to GitHub because the bug was only "
               "planted in the copy." if drill else
               "The verified fix is attached here for you to apply."), pr_url=pr or None)
        scenes.append((film.scene("5 · Ready for you", "Fix proposed, with a signed certificate",
                                  "Nothing goes live without your approval." + (f"<br>{pr}" if pr else ""),
                                  footer=f"{time.time() - t0:.0f} s from pickup to proposal"), 5))
    finally:
        sb.discard()
        try:
            evidence["seconds"] = round(time.time() - t0, 1)
            cert = api("POST", f"/worker/jobs/{jid}/certify", json=evidence)
            scenes.append((film.scene("Certificate", "Signed and chained",
                                      f"Certificate #{cert['sequence']}<br>hash {cert['hash'][:32]}…"), 4))
            mp4 = film.render(scenes, os.path.join(tempfile.mkdtemp(), f"job-{jid}.mp4"))
            api("POST", f"/worker/jobs/{jid}/artifact?kind=video", content=open(mp4, "rb").read(),
                headers={"content-type": "video/mp4"})
            log(f"[job {jid}] recording and certificate sent.")
        except Exception as exc:
            log(f"[job {jid}] could not send the recording or certificate: {exc}")


def open_pr(clone: str, job: dict, sha: str, diff: str, explanation: str) -> str:
    if os.environ.get("KINTSUGI_OPEN_PRS") != "1":
        return ""
    branch = f"kintsugi/fix-{job['job']}"
    wt = os.path.join(SANDBOXES, f"pr-{job['job']}")
    helper = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    subprocess.run(["git", "-C", clone, "worktree", "add", "-B", branch, wt, sha], check=True, capture_output=True)
    try:
        subprocess.run(["git", "apply", "-"], cwd=wt, input=diff, text=True, check=True)
        subprocess.run(["git", "-C", wt, "commit", "-qam", f"Kintsugi: fix crash on {job['capture']['request']['path']}"],
                       check=True)
        subprocess.run(["git", *helper, "-C", wt, "push", "-q", "-u", "origin", branch], check=True)
        body = (f"{explanation}\n\nKintsugi reproduced this crash in a sealed copy of the app, and this change "
                f"stopped it on 5 of 5 replays, type-checks, and passes the app's tests. The recording and "
                f"signed certificate are on your Kintsugi account.\n\n{SITE}/account/incidents/{job['job']}")
        r = subprocess.run(["gh", "pr", "create", "--repo", job["repo"], "--head", branch,
                            "--base", job.get("branch") or "main", "--title",
                            f"Kintsugi: fix crash on {job['capture']['request']['path']}", "--body", body],
                           capture_output=True, text=True)
        return r.stdout.strip().splitlines()[-1] if r.returncode == 0 else ""
    finally:
        subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", wt], capture_output=True)


def drill(name: str, connect: str) -> None:
    """Plant a known bug in a sealed copy and send one real request through it."""
    spec = DRILL_REQUESTS[name]
    clone = clone_for(spec["repo"])
    sha = subprocess.run(["git", "-C", clone, "rev-parse", "origin/main"], capture_output=True,
                         text=True).stdout.strip()
    fault = open(os.path.join(DRILLS, f"{name}.diff")).read()
    sb = NextSandbox(clone, sha, f"drill-{name}", SANDBOXES, report_to=connect.rstrip("/"),
                     release=f"drill:{name}:{sha}")
    try:
        sb.prepare(fault)
        sb.start()
        r = httpx.request(spec["method"], f"http://127.0.0.1:{sb.port}{spec['path']}", timeout=60)
        log(f"Fire drill request answered HTTP {r.status_code}; the crash report went to Kintsugi.")
        time.sleep(4)
    finally:
        sb.discard()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--drill")
    ap.add_argument("--connect")
    a = ap.parse_args()
    if not SITE or not HEADERS["x-kintsugi-worker"]:
        sys.exit("worker not configured: ~/.kintsugi/worker.env needs KINTSUGI_SITE and KINTSUGI_WORKER_TOKEN")
    os.makedirs(SANDBOXES, exist_ok=True)
    if a.drill:
        drill(a.drill, a.connect)
    while True:
        try:
            data = api("GET", "/worker/jobs")
        except httpx.HTTPError as exc:            # the site restarting is not fatal
            log(f"Kintsugi site not reachable ({type(exc).__name__}); trying again shortly.")
            if a.once:
                return
            time.sleep(30)
            continue
        for job in data["jobs"]:
            if data["spent_usd"] >= CAP_USD:
                log(f"Spending cap of ${CAP_USD:.0f} reached; only detecting and alerting from now on.")
            try:
                heal(job, data["spent_usd"])
                data["spent_usd"] = api("GET", "/worker/jobs")["spent_usd"]
            except Exception as exc:              # one bad job must not stop the worker
                log(f"[job {job['job']}] stopped: {type(exc).__name__}: {str(exc)[:200]}")
        if a.once or a.drill:
            return
        time.sleep(20)


if __name__ == "__main__":
    main()
