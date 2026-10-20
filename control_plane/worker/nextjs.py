"""A sealed copy of a Next.js app, for reproducing and testing fixes.

The copy is a git worktree of the app at the incident's release, with its
own Postgres database (empty, schema only), its own session secret, and no
production keys. Outbound network calls are blocked by a preload script, so
nothing the copy does can reach Discord, email, payments or the live
database. Failures inside the copy are reported to a sink on this machine
by the app's own Kintsugi capture agent.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time

import httpx

PG_CONTAINER = "kintsugi-sandbox-pg"
PG_PORT = 55432
PG_PASSWORD = "sandbox"          # local container only, never exposed

BLOCKER = r"""
// Kintsugi sandbox: no outbound network except this machine.
const orig = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : (input && input.url) || String(input);
  const allowed = process.env.KINTSUGI_CONTROL_PLANE || "";
  if (!/^https?:\/\/(127\.0\.0\.1|localhost)(:|\/)/.test(url) && !(allowed && url.startsWith(allowed))) {
    const e = new Error("Kintsugi sandbox blocked an outbound call to " + url.split("?")[0]);
    e.name = "KintsugiHermeticViolation";
    throw e;
  }
  return orig(input, init);
};
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sh(cmd, cwd=None, env=None, timeout=900, input=None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True,
                          timeout=timeout, input=input)


class Sink:
    """Receives the copy's capture events on 127.0.0.1."""

    def __init__(self):
        self.events: list[dict] = []
        sink = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("content-length", 0))
                try:
                    sink.events.append(json.loads(self.rfile.read(n)))
                except ValueError:
                    pass
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", free_port()), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


def ensure_postgres() -> None:
    r = sh(["docker", "ps", "-q", "-f", f"name={PG_CONTAINER}"])
    if r.stdout.strip():
        return
    sh(["docker", "rm", "-f", PG_CONTAINER])
    r = sh(["docker", "run", "-d", "--name", PG_CONTAINER, "-e", f"POSTGRES_PASSWORD={PG_PASSWORD}",
            "-p", f"127.0.0.1:{PG_PORT}:5432", "postgres:16-alpine"])
    if r.returncode:
        raise RuntimeError(f"could not start sandbox postgres: {r.stderr[-300:]}")
    for _ in range(60):
        if sh(["docker", "exec", PG_CONTAINER, "pg_isready", "-U", "postgres"]).returncode == 0:
            time.sleep(1)
            return
        time.sleep(1)
    raise RuntimeError("sandbox postgres did not become ready")


class NextSandbox:
    def __init__(self, clone: str, sha: str, name: str, workroot: str,
                 report_to: str | None = None, release: str | None = None):
        self.clone, self.sha, self.name = clone, sha, name
        self.report_to, self.release = report_to, release
        self.tree = os.path.join(workroot, name)
        self.port = free_port()
        self.db = "sbx_" + hashlib.sha1(name.encode()).hexdigest()[:10]
        self.secret = secrets.token_hex(24)
        self.sink = Sink()
        self.proc: subprocess.Popen | None = None
        self.log_path = os.path.join(workroot, f"{name}.log")

    # -- lifecycle ---------------------------------------------------------
    def prepare(self, fault_diff: str | None = None) -> None:
        if os.path.exists(self.tree):
            self.discard(keep_db=True)
        r = sh(["git", "-C", self.clone, "worktree", "add", "--detach", self.tree, self.sha])
        if r.returncode:
            raise RuntimeError(f"cannot check out {self.sha[:12]}: {r.stderr[-300:]}")
        if fault_diff:
            r = sh(["git", "apply", "--whitespace=nowarn", "-"], cwd=self.tree, input=fault_diff)
            if r.returncode:
                raise RuntimeError(f"fire-drill fault did not apply: {r.stderr[-300:]}")
        # copy-on-write clone of the installed packages (APFS), not a reinstall
        nm = os.path.join(self.clone, "node_modules")
        r = sh(["cp", "-cR", nm, os.path.join(self.tree, "node_modules")], timeout=600)
        if r.returncode:
            shutil.copytree(nm, os.path.join(self.tree, "node_modules"), symlinks=True)
        with open(os.path.join(self.tree, ".kintsugi-sandbox-blocker.cjs"), "w") as f:
            f.write(BLOCKER)
        ensure_postgres()
        sh(["docker", "exec", PG_CONTAINER, "psql", "-U", "postgres", "-c",
            f"DROP DATABASE IF EXISTS {self.db}"])
        sh(["docker", "exec", PG_CONTAINER, "psql", "-U", "postgres", "-c", f"CREATE DATABASE {self.db}"])
        r = sh(["npx", "drizzle-kit", "push", "--force"], cwd=self.tree, env=self.env(), timeout=300)
        if r.returncode:
            raise RuntimeError(f"schema push failed: {(r.stdout + r.stderr)[-400:]}")

    def env(self) -> dict:
        """Only what the copy needs; no production keys at all."""
        return {
            "PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
            "NODE_ENV": "development",
            "DATABASE_URL": f"postgres://postgres:{PG_PASSWORD}@127.0.0.1:{PG_PORT}/{self.db}",
            "SESSION_SECRET": self.secret,
            "KINTSUGI_CONTROL_PLANE": self.report_to or f"http://127.0.0.1:{self.sink.port}/sandbox",
            "RAILWAY_GIT_COMMIT_SHA": self.release or self.sha,
            "NODE_OPTIONS": f"--enable-source-maps --require "
                            f"{os.path.join(self.tree, '.kintsugi-sandbox-blocker.cjs')}",
            "NEXT_TELEMETRY_DISABLED": "1",
            "PORT": str(self.port),
        }

    def start(self) -> None:
        log = open(self.log_path, "w")
        self.proc = subprocess.Popen(["npx", "next", "dev", "-p", str(self.port)], cwd=self.tree,
                                     env=self.env(), stdout=log, stderr=subprocess.STDOUT)
        for _ in range(180):
            try:
                httpx.get(f"http://127.0.0.1:{self.port}/api/push/key", timeout=5)
                return
            except httpx.HTTPError:
                time.sleep(1)
        raise RuntimeError("the copy did not start")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def discard(self, keep_db: bool = False) -> None:
        self.stop()
        sh(["git", "-C", self.clone, "worktree", "remove", "--force", self.tree])
        shutil.rmtree(self.tree, ignore_errors=True)
        if not keep_db:
            sh(["docker", "exec", PG_CONTAINER, "psql", "-U", "postgres", "-c",
                f"DROP DATABASE IF EXISTS {self.db}"])

    # -- replay ------------------------------------------------------------
    def session_cookie(self, user_id: str) -> str:
        js = ("require('iron-session').sealData({userId: process.argv[1]}, "
              "{password: process.env.SESSION_SECRET}).then(s => console.log(s))")
        r = sh(["node", "-e", js, user_id], cwd=self.tree, env=self.env())
        return r.stdout.strip()

    def replay(self, capture: dict) -> dict:
        """One replay; failed means the copy reported an unhandled error."""
        req = capture["request"]
        headers = {k: v for k, v in (req.get("headers") or {}).items()
                   if v != "[redacted]" and k not in ("host", "content-length", "connection",
                                                      "accept-encoding", "x-forwarded-for",
                                                      "x-real-ip", "x-kintsugi-body")
                   and not k.startswith("x-railway") and not k.startswith("x-forwarded")}
        ident = capture.get("identity") or {}
        if ident.get("sub"):
            headers["cookie"] = f"dinero_session={self.session_cookie(ident['sub'])}"
        url = f"http://127.0.0.1:{self.port}{req['path']}" + (f"?{req['query']}" if req.get("query") else "")
        before = len(self.sink.events)
        try:
            r = httpx.request(req["method"], url, headers=headers,
                              content=(req.get("body") or "").encode() or None, timeout=60)
            status = r.status_code
        except httpx.HTTPError as exc:
            return {"failed": False, "harness_error": repr(exc)[:300]}
        for _ in range(30):
            if len(self.sink.events) > before:
                break
            time.sleep(0.1)
        new = self.sink.events[before:]
        if not new:
            return {"failed": False, "status_code": status}
        e = new[-1]
        e["frames"] = self.map_frames(e["frames"][:15])
        return {"failed": True, "status_code": status, "exception_type": e["exception_type"],
                "message": e.get("exception_message", "")[:300],
                "top_frame": top_frame(e["frames"], self.tree), "frames": e["frames"][:15]}

    def reproduce(self, capture: dict, runs: int = 5) -> dict:
        vs = [self.replay(capture) for _ in range(runs)]
        fails = [v for v in vs if v.get("failed")]
        errs = [v for v in vs if "harness_error" in v]
        if errs:
            return {"verdict": "refused", "reason": f"the copy could not be reached on {len(errs)}/{runs} replays", "replays": vs}
        if not fails:
            codes = sorted({v.get("status_code") for v in vs})
            return {"verdict": "refused", "reason": f"the crash did not happen again in the copy (it answered {codes}); "
                    "it probably depends on live data the copy does not have", "replays": vs}
        if any(v["exception_type"] == "KintsugiHermeticViolation" for v in fails):
            return {"verdict": "refused", "reason": "the request needs an outside service (blocked in the copy)", "replays": vs}
        if len(fails) < runs:
            return {"verdict": "refused", "reason": f"it crashed on only {len(fails)}/{runs} replays, so it is not reliable", "replays": vs}
        if len({(v["exception_type"], v["top_frame"]) for v in fails}) > 1:
            return {"verdict": "refused", "reason": "it crashed in different ways across replays", "replays": vs}
        if capture.get("exception_type") and fails[0]["exception_type"] != capture["exception_type"]:
            return {"verdict": "refused", "reason": f"the copy crashed differently ({fails[0]['exception_type']} "
                    f"instead of {capture['exception_type']})", "replays": vs}
        return {"verdict": "reproduced", "reason": f"crashed the same way on {runs} of {runs} replays",
                "signature": f"{fails[0]['exception_type']}@{fails[0]['top_frame']}", "replays": vs,
                "frames": fails[0]["frames"], "message": fails[0]["message"]}

    # -- checks for a candidate fix ----------------------------------------
    def apply(self, diff: str) -> tuple[bool, str]:
        if not diff.endswith("\n"):
            diff += "\n"
        r = sh(["git", "apply", "--whitespace=nowarn", "-"], cwd=self.tree, input=diff)
        return r.returncode == 0, r.stderr[-500:]

    def revert(self, diff: str) -> None:
        sh(["git", "apply", "-R", "--whitespace=nowarn", "-"], cwd=self.tree, input=diff)

    def typecheck(self) -> dict:
        r = sh(["npx", "tsc", "--noEmit", "-p", "."], cwd=self.tree, env=self.env(), timeout=600)
        return {"passed": r.returncode == 0, "tail": (r.stdout + r.stderr)[-800:]}

    def tests(self) -> dict:
        r = sh(["npx", "vitest", "run"], cwd=self.tree, env=self.env(), timeout=900)
        out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)
        summary = [" ".join(l.split()) for l in out.splitlines()
                   if "Tests" in l and ("passed" in l or "failed" in l)]
        return {"passed": r.returncode == 0, "summary": summary[-1] if summary else out[-300:]}

    def map_frames(self, frames: list[dict]) -> list[dict]:
        """Compiled frames -> the app's own files and lines, via source maps."""
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mapframes.cjs")
        r = sh(["node", script], cwd=self.tree, input=json.dumps(frames), timeout=60)
        try:
            return json.loads(r.stdout)
        except ValueError:
            return frames

    def source(self, rel: str) -> str:
        path = os.path.join(self.tree, rel)
        return open(path).read() if os.path.exists(path) else ""


def top_frame(frames: list[dict], tree: str) -> str:
    """The deepest frame in the app's own source, as path:line."""
    for f in frames:
        if f.get("src", "").startswith("src/"):
            return f"{f['src']}:{f['src_line']}"
    for f in frames:
        file = f.get("file", "")
        if "/src/" in file and "node_modules" not in file:
            rel = file.split("/src/", 1)[1]
            return f"src/{rel}:{f.get('line')}"
    return frames[0]["file"].rsplit("/", 1)[-1] + f":{frames[0].get('line')}" if frames else "?"
