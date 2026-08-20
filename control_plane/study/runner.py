"""One study trial: one corpus entry, one arm, one live service under load.

The trial arc is identical for every arm, which is the point — the arms are
compared on the same platform, the same fault, the same traffic:

  1. release r1 (clean) live: alpha with a real beta behind it, load running
  2. release r2 (r1 + injected fault) deployed over it
  3. the fault fires under load; the capture agent writes the event stream
  4. the arm responds: C0 observes, C1 works its repertoire,
     C2 heals ungated, C3 heals behind the full gate
  5. the outcome is timed from the traffic's first error to its recovery

MTTR is measured on the traffic stream and a post-condition probe, never on
the arm's own claims.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field

import httpx

from control_plane.deploy.controller import DeployController
from control_plane.deploy.live import ServiceProcess, free_port
from control_plane.incidents.former import signature_of
from control_plane.loop import heal
from control_plane.study.arms import C0NoRecovery, C1Repertoire, purge_pycache
from control_plane.study.loadgen import LoadGenerator
from corpus import CorpusEntry
from corpus.injector import inject

PLATFORM_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
SERVICES = os.path.join(PLATFORM_ROOT, "services")

HEALTHY_PLAN = [
    {"method": "GET", "path": "/quote", "params": {"sku": "WIDGET", "code": "SAVE10"}},
    {"method": "GET", "path": "/quote", "params": {"sku": "GADGET", "quantity": 2}},
    {"method": "POST", "path": "/orders",
     "json": {"lines": [{"sku": "SPROCKET", "quantity": 3}]}},
    {"method": "GET", "path": "/health"},
]


@dataclass
class Trial:
    entry_id: str
    arm: str
    outcome: str            # recovered | unrecovered | escalated | refused | false_fix
    mttr_seconds: float | None
    timings: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"entry": self.entry_id, "arm": self.arm,
                "outcome": self.outcome, "mttr_seconds": self.mttr_seconds,
                "timings": self.timings, "detail": self.detail}


class TrialRig:
    """The live pieces of one trial, so tests can also drive them directly.

    Every service runs from its own live tree (a full copy of services/),
    so a fault can be deployed to alpha or to beta alike. The capture file
    is shared; events carry the service that raised them, and the heal arms
    dispatch on that, which is what makes cross-service entries (fault in
    beta, crash in alpha) work without special cases.
    """

    def __init__(self, entry: CorpusEntry, workdir: str):
        self.entry = entry
        self.workdir = workdir
        self.capture_file = os.path.join(workdir, "capture.jsonl")
        self.r1 = os.path.join(workdir, "r1")
        self.trees = {name: os.path.join(workdir, f"live_{name}")
                      for name in ("alpha", "beta")}
        for dest in [self.r1, *self.trees.values()]:
            shutil.copytree(SERVICES, dest,
                            ignore=shutil.ignore_patterns("__pycache__", ".git"))
        self.procs = {
            name: ServiceProcess(
                self.trees[name], f"{name}.main:app", free_port(), "r1",
                PLATFORM_ROOT,
                extra_env={"KINTSUGI_CAPTURE_FILE": self.capture_file})
            for name in ("alpha", "beta")}
        self.load: LoadGenerator | None = None
        self._events_seen = 0

    @property
    def alpha(self) -> ServiceProcess:
        return self.procs["alpha"]

    @property
    def faulty_proc(self) -> ServiceProcess:
        """The process where the incident forms (the trigger's target)."""
        return self.procs[self.entry.trigger_service]

    def start(self) -> None:
        self.procs["beta"].start()
        self.alpha.extra_env["BETA_URL"] = \
            f"http://127.0.0.1:{self.procs['beta'].port}"
        self.alpha.start()
        trigger = dict(self.entry.trigger)
        if self.entry.trigger_service != "alpha":
            trigger["url"] = (f"http://127.0.0.1:"
                              f"{self.faulty_proc.port}{trigger['path']}")
        plan = HEALTHY_PLAN + self.entry.setup + [trigger]
        workers = 8 if self.entry.requires_concurrency else 1
        self.load = LoadGenerator(f"http://127.0.0.1:{self.alpha.port}", plan,
                                  interval=0.01, workers=workers)
        self.load.start()

    def stop(self) -> None:
        if self.load:
            self.load.stop()
        for proc in self.procs.values():
            proc.stop()

    def deploy_fault(self) -> float:
        """Release r2: the clean tree plus the entry's fault, deployed to
        the service the entry targets."""
        faulty = inject(self.entry, SERVICES, os.path.join(self.workdir, "r2"))
        target = self.procs[self.entry.service]
        DeployController._sync(faulty, target.tree)
        purge_pycache(target.tree)
        target.release = "r2"
        target.restart()
        return time.time()

    def new_events(self) -> list[dict]:
        if not os.path.exists(self.capture_file):
            return []
        with open(self.capture_file) as fh:
            lines = fh.readlines()
        fresh = [json.loads(line) for line in lines[self._events_seen:]]
        self._events_seen = len(lines)
        return fresh

    def wait_for_event(self, timeout: float = 15.0) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            events = self.new_events()
            if events:
                return events[0]
            time.sleep(0.05)
        return None

    def probe(self, attempts: int = 5) -> bool:
        """Post-condition: setup then trigger, no 5xx across `attempts`.
        Setup runs against alpha (where state lives); the trigger goes to
        the entry's trigger service. Race entries fire the trigger from
        overlapping threads, since sequential requests cannot race."""
        alpha_base = f"http://127.0.0.1:{self.alpha.port}"
        trigger_base = f"http://127.0.0.1:{self.faulty_proc.port}"
        for _ in range(attempts):
            with httpx.Client(timeout=5.0, base_url=alpha_base) as client:
                for request in self.entry.setup:
                    if client.request(request["method"], request["path"],
                                      params=request.get("params"),
                                      json=request.get("json")).status_code >= 500:
                        return False
            statuses: list[int] = []

            def fire():
                with httpx.Client(timeout=5.0, base_url=trigger_base) as c:
                    statuses.append(c.request(
                        self.entry.trigger["method"], self.entry.trigger["path"],
                        params=self.entry.trigger.get("params"),
                        json=self.entry.trigger.get("json")).status_code)

            if self.entry.requires_concurrency:
                threads = [threading.Thread(target=fire) for _ in range(8)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            else:
                fire()
            if any(s >= 500 for s in statuses):
                return False
        return True

    def traffic_clean(self, quiet: float = 0.8, timeout: float = 8.0) -> bool:
        """True once the live traffic stream shows no 5xx for `quiet`
        seconds. The probe alone is not recovery: a fault that fans out
        keeps erroring on paths the entry's trigger never touches."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            window = self.load.since(time.time() - quiet)
            if window and not any(s.status >= 500 for s in window):
                return True
            time.sleep(0.1)
        return False

    def deploy_patch(self, record: str, patched_tree: str,
                     service: str | None = None) -> None:
        from control_plane.gate.verifier import discard_patched_tree

        target = self.procs[service or self.entry.trigger_service]
        DeployController(target.tree).deploy(record, patched_tree)
        purge_pycache(target.tree)
        target.restart()
        discard_patched_tree(patched_tree)   # rsynced into the live tree


def run_trial(entry: CorpusEntry, arm: str, workdir: str, *,
              complete=None, observe_seconds: float = 1.5,
              suite_path: str | None = None, certificates=None,
              practice_bank=None, require_reproduction: bool | None = None,
              max_candidates: int | None = None) -> Trial:
    rig = TrialRig(entry, workdir)
    timings: dict = {}
    try:
        rig.start()
        timings["fault_deployed"] = rig.deploy_fault()

        first_event = rig.wait_for_event()
        if first_event is None:
            return Trial(entry.id, arm, "fault_never_fired", None, timings)
        timings["detected"] = time.time()
        errors = rig.load.errors_since(timings["fault_deployed"])
        timings["first_error"] = errors[0].t if errors else timings["detected"]

        if arm == "C0":
            time.sleep(observe_seconds)
            # the registered definition: probe AND a clean traffic stream.
            # A probabilistic fault can slip a five-round probe by luck,
            # but not a live stream still showing its 5xx samples.
            outcome = ("recovered" if rig.probe() and rig.traffic_clean()
                       else "unrecovered")
            return Trial(entry.id, arm, outcome, None, timings,
                         {"actions": ["observed"]})

        if arm == "C1":
            return _run_c1(rig, first_event, timings)

        if arm in ("C2", "C3"):
            return _run_heal_arm(rig, arm, first_event, timings,
                                 complete=complete, suite_path=suite_path,
                                 certificates=certificates,
                                 practice_bank=practice_bank,
                                 require_reproduction=require_reproduction,
                                 max_candidates=max_candidates)

        raise ValueError(f"unknown arm {arm}")
    finally:
        rig.stop()


def _finish(rig: TrialRig, trial: Trial,
            recovered_at: float | None = None) -> Trial:
    if trial.outcome == "recovered":
        # MTTR runs to the probe pass (the registered definition), not to
        # the later traffic-clean confirmation
        trial.timings["recovered"] = recovered_at or time.time()
        trial.mttr_seconds = round(
            trial.timings["recovered"] - trial.timings["first_error"], 3)
    return trial


def _run_c1(rig: TrialRig, first_event: dict, timings: dict) -> Trial:
    # the repertoire acts on the service whose release changed last (r2):
    # restart it, then roll IT back. Deploy-aware, like real runbooks.
    arm = C1Repertoire(rig.procs[rig.entry.service], rig.r1)
    signature = signature_of(first_event)
    deadline = time.time() + 30
    arm.on_incident(signature)
    rig.new_events()   # events from before the action are not recurrence
    while time.time() < deadline:
        if rig.probe():
            probe_passed = time.time()
            if rig.traffic_clean():
                report = [a.action for a in arm.report.actions]
                return _finish(rig, Trial(rig.entry.id, "C1", "recovered",
                                          None, timings,
                                          {"actions": report}),
                               recovered_at=probe_passed)
        event = rig.wait_for_event(timeout=5.0)
        if event is None:
            break
        arm.on_incident(signature_of(event))
        rig.new_events()
        if arm.report.took("escalated"):
            break
    report = [a.action for a in arm.report.actions]
    outcome = "escalated" if arm.report.took("escalated") else "unrecovered"
    return Trial(rig.entry.id, "C1", outcome, None, timings,
                 {"actions": report})


MAX_INCIDENTS_PER_TRIAL = 4


def _incident_key(event: dict) -> tuple[str, str]:
    return (event["exception_type"], event["request"]["path"])


def _run_heal_arm(rig: TrialRig, arm: str, first_event: dict, timings: dict,
                  *, complete, suite_path, certificates=None,
                  practice_bank=None, require_reproduction: bool | None = None,
                  max_candidates: int | None = None) -> Trial:
    """Heal successive incidents until the traffic is clean.

    One injected fault can fan out into several distinct incidents (a
    dependency fault breaks every consumer path that touches it), so a
    single heal is not the protocol; responding to what the capture stream
    shows is. An incident RECURRING after its own patch deployed is the
    false-fix signal; a DIFFERENT incident is the next piece of work.

    `require_reproduction` / `max_candidates` override the arm defaults for
    the study's guard-off and regeneration-off ablations.
    """
    gated = (arm == "C3") if require_reproduction is None else require_reproduction
    detail: dict = {"incidents": []}
    healed_keys: set[tuple[str, str]] = set()
    event = first_event

    for _ in range(MAX_INCIDENTS_PER_TRIAL):
        # heal the service that raised the incident; for cross-service
        # faults that is the consumer, and its patch is necessarily defensive
        svc = event["service"]
        notes = practice_bank.notes_for(event) if practice_bank else None
        outcome = heal(event, f"{svc}.main:app", rig.trees[svc],
                       complete=complete, incident_id=rig.entry.id,
                       suite_path=suite_path, memory_notes=notes,
                       require_reproduction=gated,
                       max_candidates=max_candidates)
        timings["heal_finished"] = time.time()
        detail["incidents"].append(
            {"signature": outcome.signature, "heal_status": outcome.status,
             "heal_reason": outcome.reason,
             "candidates": len(outcome.candidates)})
        detail["heal_status"] = outcome.status
        detail["heal_reason"] = outcome.reason
        detail["candidates"] = sum(i["candidates"]
                                   for i in detail["incidents"])

        if outcome.status != "deployed":
            refused = outcome.reason.startswith("reproduction refused")
            return Trial(rig.entry.id, arm,
                         "refused" if refused else "escalated",
                         None, timings, detail)

        rig.deploy_patch(outcome.verification_record, outcome.patched_tree,
                         service=svc)
        timings["patch_deployed"] = time.time()
        if certificates is not None:
            cert = certificates.issue(event, outcome, f"{svc}.main:app")
            detail.setdefault("certificates", []).append(cert["hash"])
        healed_keys.add(_incident_key(event))
        rig.new_events()          # discard pre-deploy captures

        if rig.probe():
            probe_passed = time.time()
            if rig.traffic_clean():
                return _finish(rig, Trial(rig.entry.id, arm, "recovered",
                                          None, timings, detail),
                               recovered_at=probe_passed)

        event = rig.wait_for_event(timeout=5.0)
        if event is None or _incident_key(event) in healed_keys:
            # probe failing with nothing new, or a healed incident is back:
            # the deployed patch did not do its job
            return Trial(rig.entry.id, arm, "false_fix", None, timings,
                         detail)

    return Trial(rig.entry.id, arm, "escalated", None, timings,
                 dict(detail, heal_reason="incident budget exhausted"))
