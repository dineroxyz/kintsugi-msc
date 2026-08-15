"""Deploy controller and metric guard.

The controller is the only component that can alter a managed service, and
its interface accepts exactly one input type: a gate-signed verification
record. That type constraint — checked cryptographically, not by convention
— is the enforcement-by-construction of the invariant in chapter three,
section 3.3. There is no deploy path that skips the gate because there is
no way to construct the record without it.

The metric guard is pure decision logic over two windows of observations,
kept free of process orchestration so the study can exercise it exhaustively.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


class DeployRefused(RuntimeError):
    """Raised when something other than a valid gate record asks to deploy."""


@dataclass
class GuardThresholds:
    max_error_rate_increase: float = 0.02    # absolute increase tolerated
    max_p95_latency_ratio: float = 1.50      # canary p95 / baseline p95
    min_throughput_ratio: float = 0.70       # canary rps / baseline rps


@dataclass
class WindowStats:
    requests: int
    errors: int
    p95_latency_ms: float

    @property
    def error_rate(self) -> float:
        return self.errors / self.requests if self.requests else 0.0


def guard_decision(baseline: WindowStats, canary: WindowStats,
                   thresholds: GuardThresholds = GuardThresholds()) -> dict:
    """Promote, or breach with the reasons. A repair that degrades any
    observed signal is not a repair, whatever the gate concluded."""
    breaches = []
    if canary.error_rate > baseline.error_rate + thresholds.max_error_rate_increase:
        breaches.append(f"error rate {canary.error_rate:.3f} vs baseline "
                        f"{baseline.error_rate:.3f}")
    if baseline.p95_latency_ms > 0 and (
            canary.p95_latency_ms / baseline.p95_latency_ms
            > thresholds.max_p95_latency_ratio):
        breaches.append(f"p95 latency {canary.p95_latency_ms:.0f}ms vs baseline "
                        f"{baseline.p95_latency_ms:.0f}ms")
    if baseline.requests > 0 and (
            canary.requests / baseline.requests < thresholds.min_throughput_ratio):
        breaches.append(f"throughput fell to {canary.requests}/{baseline.requests} "
                        "of baseline")
    return {"promote": not breaches, "breaches": breaches}


class DeployController:
    """Applies a verified patched tree to the live service checkout.

    `deploy` demands the signed record and re-verifies it; `rollback`
    restores the pre-deploy state from the snapshot taken at deploy time.
    """

    def __init__(self, live_tree: str):
        self.live_tree = live_tree
        self._snapshot: str | None = None

    def deploy(self, record_json: str | None, patched_tree: str) -> dict:
        from control_plane.gate.verifier import verify_record

        if not record_json:
            raise DeployRefused("no verification record presented")
        record = json.loads(record_json)
        if not verify_record(record["payload"], record["hmac"]):
            raise DeployRefused("verification record failed signature check")

        self._snapshot = self.live_tree + ".pre-deploy"
        shutil.rmtree(self._snapshot, ignore_errors=True)
        shutil.copytree(self.live_tree, self._snapshot,
                        ignore=shutil.ignore_patterns("__pycache__"))

        self._sync(patched_tree, self.live_tree)
        return {"deployed": True, "payload": record["payload"]}

    def rollback(self) -> dict:
        if not self._snapshot:
            raise DeployRefused("nothing to roll back to")
        self._sync(self._snapshot, self.live_tree)
        return {"rolled_back": True}

    @staticmethod
    def _sync(src: str, dst: str) -> None:
        subprocess.run(
            ["rsync", "-a", "--delete", "--exclude", "__pycache__",
             "--exclude", ".git", src.rstrip("/") + "/", dst.rstrip("/") + "/"],
            check=True, capture_output=True)
