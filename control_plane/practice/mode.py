"""Self-practice: the platform rehearses failures while idle.

A rehearsal injects one corpus fault into a SHADOW copy of the services,
fires its trigger there, and runs the full gated loop against the shadow.
Nothing is deployed anywhere; what is kept is the evidence: the capture,
the reproduction verdicts (or the refusal and its reason), the compiled
regression test, and the gate-admitted candidate patch when one exists.

The bank is the platform's memory. When a real incident later resembles a
banked rehearsal (same service, exception type, and path shape), the
rehearsal's evidence is handed to diagnosis as memory notes, which is what
"practising while idle" buys at three in the morning: the reproduction is
already known to work and a known-good patch shape rides along.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from control_plane.loop import heal
from corpus import CorpusEntry
from corpus.injector import inject
from libs.kintsugi_capture import CaptureMiddleware

PLATFORM_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
SERVICES = os.path.join(PLATFORM_ROOT, "services")


def practice_key(service: str, exception_type: str, path: str) -> str:
    """Release-independent resemblance key for recalling rehearsals."""
    shape = "/".join("{}" if seg.isdigit() else seg
                     for seg in path.split("/"))
    return f"{service}:{exception_type}:{shape}"


@dataclass
class Rehearsal:
    entry_id: str
    status: str                  # would_heal | refused | escalated | no_fire
    key: str = ""
    signature: str | None = None
    reason: str = ""
    patch: str | None = None
    regression_test: str | None = None
    rehearsed_at: str = ""
    capture: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class PracticeBank:
    """Append-only store of rehearsal outcomes, one JSON line each."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def bank(self, rehearsal: Rehearsal) -> None:
        with open(self.path, "a") as fh:
            fh.write(json.dumps(rehearsal.to_dict()) + "\n")

    def all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        return [json.loads(line) for line in open(self.path)]

    def counts(self) -> dict:
        counts: dict[str, int] = {}
        for row in self.all():
            counts[row["entry_id"]] = counts.get(row["entry_id"], 0) + 1
        return counts

    def notes_for(self, capture: dict) -> list[str]:
        """Memory notes for a real incident that resembles a rehearsal."""
        key = practice_key(capture.get("service", ""),
                           capture.get("exception_type", ""),
                           capture.get("request", {}).get("path", ""))
        notes = []
        for row in self.all():
            if row.get("key") != key:
                continue
            if row["status"] == "would_heal" and row.get("patch"):
                notes.append(
                    "This failure shape was rehearsed in self-practice on "
                    f"{row['rehearsed_at']} and healed. The patch the gate "
                    f"admitted then:\n{row['patch']}")
            elif row["status"] == "refused":
                notes.append(
                    "This failure shape was rehearsed and REFUSED "
                    f"({row['reason']}); expect reproduction to refuse again.")
        return notes


def _fire_in_shadow(entry: CorpusEntry, shadow: str) -> dict | None:
    """Import the shadow service in-process and fire the entry's trigger."""
    sys.path.insert(0, shadow)
    for name in [n for n in sys.modules if n.startswith(("alpha", "beta"))]:
        del sys.modules[name]
    try:
        module = importlib.import_module(f"{entry.trigger_service}.main")
    finally:
        sys.path.remove(shadow)
    events: list[dict] = []
    module.app.user_middleware.clear()
    module.app.middleware_stack = None
    module.app.add_middleware(CaptureMiddleware,
                              service=entry.trigger_service,
                              release="practice", deliver=events.append)
    client = TestClient(module.app, raise_server_exceptions=False)
    for request in entry.setup:
        client.request(request["method"], request["path"],
                       params=request.get("params"),
                       json=request.get("json"))

    def fire_once() -> None:
        client.request(entry.trigger["method"], entry.trigger["path"],
                       params=entry.trigger.get("params"),
                       json=entry.trigger.get("json"))

    if entry.requires_concurrency:
        for _ in range(10):
            threads = [threading.Thread(target=fire_once) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            if events:
                break
    else:
        for _ in range(1 if entry.deterministic else 30):
            fire_once()
            if events:
                break
    return events[-1] if events else None


def rehearse(entry: CorpusEntry, workdir: str, *, complete=None) -> Rehearsal:
    """One rehearsal: shadow, inject, fire, run the gated loop, keep the
    evidence. Deploys nothing."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    shadow = inject(entry, SERVICES, os.path.join(workdir, "shadow"))
    try:
        capture = _fire_in_shadow(entry, shadow)
        if capture is None:
            return Rehearsal(entry.id, "no_fire", rehearsed_at=stamp)

        outcome = heal(capture, f"{entry.trigger_service}.main:app", shadow,
                       complete=complete, incident_id=f"practice-{entry.id}",
                       suite_path=None)
        key = practice_key(capture["service"], capture["exception_type"],
                           capture["request"]["path"])
        if outcome.status == "deployed":     # gate-admitted; NOT deployed here
            return Rehearsal(entry.id, "would_heal", key=key,
                             signature=outcome.signature,
                             patch=outcome.candidates[-1]["diff"],
                             regression_test=outcome.regression_test,
                             rehearsed_at=stamp, capture=capture)
        refused = outcome.reason.startswith("reproduction refused")
        return Rehearsal(entry.id, "refused" if refused else "escalated",
                         key=key, signature=outcome.signature,
                         reason=outcome.reason, rehearsed_at=stamp,
                         capture=capture)
    finally:
        shutil.rmtree(shadow, ignore_errors=True)


class PracticeScheduler:
    """Round-robin rehearsals over the corpus whenever the platform is idle.

    `idle` is a callable answering "is now a good time?"; production wires
    it to live traffic levels, tests wire it to a lambda. `run(n)` performs
    up to n scheduling ticks; the overnight worker just calls it with a
    large n and a sleep between ticks.
    """

    def __init__(self, corpus: list[CorpusEntry], bank: PracticeBank,
                 workroot: str, *, idle=lambda: True, complete=None):
        self.corpus = corpus
        self.bank = bank
        self.workroot = workroot
        self.idle = idle
        self.complete = complete

    def next_entry(self) -> CorpusEntry:
        counts = self.bank.counts()
        return min(self.corpus, key=lambda e: (counts.get(e.id, 0), e.id))

    def run(self, ticks: int, *, pause: float = 0.0) -> list[Rehearsal]:
        done = []
        for tick in range(ticks):
            if not self.idle():
                continue
            entry = self.next_entry()
            workdir = os.path.join(self.workroot, f"tick-{tick}")
            os.makedirs(workdir, exist_ok=True)
            rehearsal = rehearse(entry, workdir, complete=self.complete)
            self.bank.bank(rehearsal)
            done.append(rehearsal)
            if pause:
                time.sleep(pause)
        return done
