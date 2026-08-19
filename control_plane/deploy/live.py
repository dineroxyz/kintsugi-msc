"""Live canary orchestration: real processes, real traffic, real decision.

Chapter three, section 3.6.5, made concrete. A verified patch is exposed to a
fraction of genuine HTTP traffic through a splitting proxy while the metric
guard compares the canary's window against a baseline recorded immediately
beforehand. Promotion deploys the patched tree over the live one and restarts
it; a breach kills the canary and leaves the live service untouched.

Three pieces:
  ServiceProcess    — a managed uvicorn subprocess serving one tree
  TrafficSplitProxy — in-process ASGI proxy that routes a fraction of traffic
                      to the canary and records per-request outcomes
  LiveCanaryController — baseline window, canary window, guard decision,
                      promote or discard
"""

from __future__ import annotations

import os
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field

import httpx
import uvicorn

from control_plane.deploy.controller import (DeployController, GuardThresholds,
                                             WindowStats, guard_decision)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServiceProcess:
    """One managed service running as its own uvicorn subprocess."""

    def __init__(self, tree: str, spec: str, port: int, release: str,
                 platform_root: str, extra_env: dict | None = None):
        self.tree, self.spec, self.port = tree, spec, port
        self.release, self.platform_root = release, platform_root
        self.extra_env = extra_env or {}
        self.proc: subprocess.Popen | None = None
        self.stderr_path: str | None = None

    def start(self, timeout: float = 20.0) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = self.tree + os.pathsep + self.platform_root \
            + os.pathsep + env.get("PYTHONPATH", "")
        env["KINTSUGI_RELEASE"] = self.release
        env.update(self.extra_env)
        module, attr = self.spec.split(":")
        # stderr goes to a file, never a pipe: an undrained pipe buffer
        # fills with 500-tracebacks and blocks the service mid-request
        fd, self.stderr_path = tempfile.mkstemp(prefix="kintsugi-svc-", suffix=".log")
        stderr_file = os.fdopen(fd, "wb")
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", f"{module}:{attr}",
                 "--host", "127.0.0.1", "--port", str(self.port),
                 "--log-level", "error"],
                cwd=self.tree, env=env,
                stdout=subprocess.DEVNULL, stderr=stderr_file)
        finally:
            stderr_file.close()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), 0.3):
                    return
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError(
                        "service exited during startup: "
                        + open(self.stderr_path, "rb").read().decode()[-1500:])
                time.sleep(0.15)
        raise RuntimeError(f"service on :{self.port} not ready in {timeout}s")

    def restart(self) -> None:
        self.stop()
        self.start()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


@dataclass
class _Sample:
    upstream: str
    status: int
    latency_ms: float


class TrafficSplitProxy:
    """ASGI reverse proxy with a runtime-adjustable canary fraction.

    Every request is routed to `main` or, with probability `fraction`, to
    `canary`, and its outcome recorded. The guard's windows are computed
    from these samples, so the decision is made on the same traffic the
    users generated, not on synthetic probes.
    """

    def __init__(self, main_url: str, port: int):
        self.main_url = main_url
        self.canary_url: str | None = None
        self.fraction = 0.0
        self.port = port
        self.samples: list[_Sample] = []
        self._lock = threading.Lock()
        self._server: uvicorn.Server | None = None
        self._client = httpx.AsyncClient(timeout=10.0)

    # -- ASGI app ---------------------------------------------------------
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break

        use_canary = self.canary_url and random.random() < self.fraction
        upstream = self.canary_url if use_canary else self.main_url
        url = upstream + scope["path"]
        if scope.get("query_string"):
            url += "?" + scope["query_string"].decode()

        headers = [(k.decode(), v.decode()) for k, v in scope.get("headers", [])
                   if k.decode().lower() not in ("host", "content-length")]
        t0 = time.perf_counter()
        try:
            resp = await self._client.request(
                scope["method"], url, content=body, headers=dict(headers))
            status, content = resp.status_code, resp.content
            ctype = resp.headers.get("content-type", "application/json")
        except httpx.HTTPError:
            status, content, ctype = 502, b'{"error":"upstream unreachable"}', "application/json"
        latency = (time.perf_counter() - t0) * 1000

        with self._lock:
            self.samples.append(_Sample(
                "canary" if use_canary else "main", status, latency))

        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", ctype.encode())]})
        await send({"type": "http.response.body", "body": content})

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        config = uvicorn.Config(self, host="127.0.0.1", port=self.port,
                                log_level="error")
        self._server = uvicorn.Server(config)
        thread = threading.Thread(target=self._server.run, daemon=True)
        thread.start()
        deadline = time.time() + 10
        while time.time() < deadline and not self._server.started:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("proxy failed to start")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True

    # -- windows ----------------------------------------------------------
    def reset_window(self) -> None:
        with self._lock:
            self.samples.clear()

    def window(self, upstream: str) -> WindowStats:
        with self._lock:
            picked = [s for s in self.samples if s.upstream == upstream]
        if not picked:
            return WindowStats(0, 0, 0.0)
        latencies = sorted(s.latency_ms for s in picked)
        p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))]
        return WindowStats(
            requests=len(picked),
            errors=sum(1 for s in picked if s.status >= 500),
            p95_latency_ms=p95)


@dataclass
class CanaryReport:
    promoted: bool
    baseline: WindowStats
    canary: WindowStats
    decision: dict
    stages: list[dict] = field(default_factory=list)


class LiveCanaryController:
    """Run the canary protocol for one verified patch against live traffic.

    `traffic` is a callable that drives one batch of requests at the proxy;
    the study's load generator and the tests both supply their own.
    """

    def __init__(self, main: ServiceProcess, proxy: TrafficSplitProxy,
                 platform_root: str,
                 thresholds: GuardThresholds = GuardThresholds()):
        self.main, self.proxy = main, proxy
        self.platform_root = platform_root
        self.thresholds = thresholds

    def run(self, record_json: str, patched_tree: str, spec: str,
            traffic, *, fraction: float = 0.3) -> CanaryReport:
        # 1. baseline window on the live service alone
        self.proxy.reset_window()
        traffic()
        baseline = self.proxy.window("main")

        # 2. start the canary from the patched tree and split traffic
        canary_port = free_port()
        canary = ServiceProcess(os.path.join(patched_tree), spec,
                                canary_port, self.main.release + "+canary",
                                self.platform_root)
        canary.start()
        self.proxy.canary_url = f"http://127.0.0.1:{canary_port}"
        self.proxy.fraction = fraction
        try:
            self.proxy.reset_window()
            traffic()
            canary_window = self.proxy.window("canary")
            main_window = self.proxy.window("main")

            # the canary only receives its assigned fraction of traffic,
            # so its throughput is judged against that expectation
            phase_total = canary_window.requests + main_window.requests
            expected = max(1, round(phase_total * fraction))
            decision = guard_decision(baseline, canary_window, self.thresholds,
                                      expected_requests=expected)
            if not decision["promote"]:
                return CanaryReport(False, baseline, canary_window, decision)

            # 3. promote: deploy the verified tree over the live one and
            # restart it; the deploy controller re-verifies the record
            controller = DeployController(self.main.tree)
            controller.deploy(record_json, patched_tree)
            self.main.restart()
            return CanaryReport(True, baseline, canary_window, decision)
        finally:
            self.proxy.fraction = 0.0
            self.proxy.canary_url = None
            canary.stop()
