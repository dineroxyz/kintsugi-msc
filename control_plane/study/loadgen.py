"""Deterministic load generator for the study runs.

Cycles a fixed request plan round-robin against a base URL from a background
thread, timestamping every outcome. Round-robin rather than random so that
two runs of the same plan offer identical traffic; the study's seeds live in
the corpus and the model, not in the load.

Connection failures are recorded as status 599 so a dead service is visible
in the same stream as an erroring one.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx


@dataclass
class Sample:
    t: float
    method: str
    path: str
    status: int


class LoadGenerator:
    def __init__(self, base_url: str, plan: list[dict], *,
                 interval: float = 0.02, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.plan = plan
        self.interval = interval
        self.timeout = timeout
        self.samples: list[Sample] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> list[Sample]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.timeout + 2)
        return self.samples

    def _run(self) -> None:
        i = 0
        with httpx.Client(timeout=self.timeout) as client:
            while not self._stop.is_set():
                request = self.plan[i % len(self.plan)]
                i += 1
                try:
                    resp = client.request(request["method"],
                                          self.base_url + request["path"],
                                          params=request.get("params"),
                                          json=request.get("json"))
                    status = resp.status_code
                except httpx.HTTPError:
                    status = 599
                with self._lock:
                    self.samples.append(Sample(time.time(), request["method"],
                                               request["path"], status))
                self._stop.wait(self.interval)

    # -- observations ------------------------------------------------------
    def since(self, t: float) -> list[Sample]:
        with self._lock:
            return [s for s in self.samples if s.t >= t]

    def errors_since(self, t: float) -> list[Sample]:
        return [s for s in self.since(t) if s.status >= 500]

    def wait_for_error(self, timeout: float = 10.0) -> Sample | None:
        """Block until the stream shows a 5xx; None on timeout."""
        t0 = time.time()
        seen = len(self.samples)
        while time.time() - t0 < timeout:
            with self._lock:
                fresh = self.samples[seen:]
                seen = len(self.samples)
            for sample in fresh:
                if sample.status >= 500:
                    return sample
            time.sleep(0.05)
        return None
