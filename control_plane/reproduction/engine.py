"""Reproduction engine: convert a captured incident into a deterministic
failing test, or refuse.

Chapter three, section 3.5.2. The engine runs the replay harness N times,
each in a fresh subprocess, and admits the reproduction only when every run
fails with the same signature (exception type + deepest application frame).
Anything less — an intermittent failure, a different exception between runs,
a replay that reaches for the network — is a refusal, and refusal is a first
class outcome that escalates to a human rather than a degraded attempt.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

DEFAULT_RUNS = 5


@dataclass
class ReproductionResult:
    status: str                      # "reproduced" | "refused"
    reason: str                      # human-readable, goes in the certificate
    signature: str | None            # "ExcType@file:line" when reproduced
    verdicts: list[dict] = field(default_factory=list)

    @property
    def reproduced(self) -> bool:
        return self.status == "reproduced"


def _run_harness_once(capture_path: str, service_spec: str,
                      workdir: str | None, timeout: int) -> dict:
    env = dict(os.environ)
    env.pop("KINTSUGI_REPLAY_FILE", None)   # harness sets its own
    env.pop("KINTSUGI_CAPTURE_FILE", None)
    root = workdir or os.getcwd()
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "control_plane.reproduction.harness",
         capture_path, service_spec],
        capture_output=True, text=True, timeout=timeout, env=env,
        cwd=os.getcwd(),
    )
    if proc.returncode != 0:
        return {"failed": False, "harness_error": proc.stderr[-2000:]}
    return json.loads(proc.stdout.strip().splitlines()[-1])


def reproduce(capture: dict, service_spec: str, *, workdir: str | None = None,
              runs: int = DEFAULT_RUNS, timeout: int = 60) -> ReproductionResult:
    """Attempt deterministic reproduction of a captured failure.

    workdir, when given, is a checkout of the service at the incident's
    release; it is prepended to PYTHONPATH so the harness imports the code
    that was actually running. Without it the current tree is used, and the
    certificate records that.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(capture, f)
        capture_path = f.name

    verdicts = []
    try:
        for _ in range(runs):
            verdicts.append(_run_harness_once(capture_path, service_spec,
                                              workdir, timeout))
    finally:
        os.unlink(capture_path)

    errors = [v for v in verdicts if "harness_error" in v]
    if errors:
        return ReproductionResult("refused", f"harness error on {len(errors)}/{runs} runs",
                                  None, verdicts)

    hermetic = [v for v in verdicts if v.get("hermetic_violation")]
    if hermetic:
        return ReproductionResult(
            "refused",
            "replay attempted an outbound call the capture did not record; "
            "the incident depends on state outside the capture", None, verdicts)

    failures = [v for v in verdicts if v["failed"]]
    if not failures:
        return ReproductionResult(
            "refused", "captured failure did not recur under replay", None, verdicts)
    if len(failures) < runs:
        return ReproductionResult(
            "refused",
            f"failure recurred on only {len(failures)}/{runs} replays; "
            "non-deterministic, unusable as an acceptance criterion", None, verdicts)

    signatures = {f"{v['exception_type']}@{v['top_frame']}" for v in failures}
    if len(signatures) > 1:
        return ReproductionResult(
            "refused", f"failure signature varied across replays: {sorted(signatures)}",
            None, verdicts)

    return ReproductionResult("reproduced",
                              f"failed identically on {runs}/{runs} replays",
                              signatures.pop(), verdicts)
