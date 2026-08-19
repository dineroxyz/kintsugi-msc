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
                 interval: float = 0.02, timeout: float = 5.0,
                 workers: int = 1):
        self.base_url = base_url.rstrip("/")
        self.plan = plan
        self.interval = interval
        self.timeout = timeout
        self.workers = workers      # >1 makes requests genuinely overlap,
                                    # which race faults need to fire
        self.samples: list[Sample] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._threads = [threading.Thread(target=self._run, args=(w,),
                                          daemon=True)
                         for w in range(self.workers)]
        for thread in self._threads:
            thread.start()

    def stop(self) -> list[Sample]:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=self.timeout + 2)
        return self.samples

    def _run(self, offset: int) -> None:
        i = offset      # workers start staggered through the plan
        with httpx.Client(timeout=self.timeout) as client:
            while not self._stop.is_set():
                request = self.plan[i % len(self.plan)]
                i += 1
                url = request.get("url") or self.base_url + request["path"]
                try:
                    resp = client.request(request["method"], url,
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
