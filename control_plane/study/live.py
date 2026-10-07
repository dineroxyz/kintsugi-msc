"""One live, watchable heal: the study's trial arc with every step broadcast.

The console's Live view calls run_live() and turns its events into the
picture: the shop's traffic, the bug going live, the capture, the replays,
the patch as the model writes it, the gate's checks, the deploy, the
recovery and the certificate. The steps are the study's own (TrialRig,
heal(), the deploy controller, the probe); this module only narrates them,
so what the screen shows is what the platform did.

Each event is a dict: {"t": seconds since the run began, "kind": ..., ...}.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time

from control_plane.diagnosis.proposer import anthropic_streaming_complete
from control_plane.loop import heal
from control_plane.study.runner import TrialRig, _incident_key
from corpus import CorpusEntry

WARMUP_SECONDS = 2.5       # healthy traffic before the bug goes live
COOLDOWN_SECONDS = 3.0     # traffic after the outcome, so recovery is seen
MAX_INCIDENTS = 4


def run_live(entry: CorpusEntry, emit, *, complete=None,
             certificates=None) -> dict:
    """Run one heal of `entry` and call emit(event) for every step.
    `complete` overrides the model (tests pass a scripted one)."""
    t0 = time.time()

    def send(kind: str, **data) -> None:
        emit({"t": round(time.time() - t0, 3), "kind": kind, **data})

    workdir = tempfile.mkdtemp(prefix="kintsugi-live-")
    rig = TrialRig(entry, workdir)
    stop_pump = threading.Event()
    result: dict = {"outcome": "error", "mttr_seconds": None}

    def pump() -> None:
        """Forward the load generator's samples as they arrive."""
        seen = 0
        while not stop_pump.is_set():
            if rig.load is not None:
                with rig.load._lock:
                    fresh = rig.load.samples[seen:]
                    seen = len(rig.load.samples)
                if fresh:
                    send("traffic", samples=[[round(s.t - t0, 3), s.status, s.path]
                                             for s in fresh])
            stop_pump.wait(0.12)

    # the model's patch, forwarded in small batches as it is written
    buffer: list[str] = []
    last_flush = [time.time()]

    def on_text(piece: str) -> None:
        buffer.append(piece)
        if time.time() - last_flush[0] > 0.06:
            send("token", text="".join(buffer))
            buffer.clear()
            last_flush[0] = time.time()

    def flush() -> None:
        if buffer:
            send("token", text="".join(buffer))
            buffer.clear()

    model = complete or anthropic_streaming_complete(on_text)

    def on_heal_event(kind: str, **data) -> None:
        if kind in ("gate_result", "escalated", "refused"):
            flush()
        send(kind, **data)

    pump_thread = threading.Thread(target=pump, daemon=True)
    try:
        send("start", entry=entry.id, title=entry.title, service=entry.service,
             trigger=f"{entry.trigger['method']} {entry.trigger['path']}",
             expected=entry.expected_c3)
        rig.start()
        pump_thread.start()
        send("traffic_started")
        time.sleep(WARMUP_SECONDS)

        rig.deploy_fault()
        fault_at = time.time()
        send("fault_deployed", service=entry.service)

        event = rig.wait_for_event()
        if event is None:
            send("no_fire")
            result["outcome"] = "fault_never_fired"
            return result
        errors = rig.load.since(fault_at)
        first_error = next((s.t for s in errors if s.status >= 500), time.time())
        send("captured", exception_type=event.get("exception_type"),
             message=str(event.get("exception_message", ""))[:160],
             method=event.get("request", {}).get("method"),
             path=event.get("request", {}).get("path"),
             service=event.get("service"))

        healed_keys: set = set()
        certs: list[dict] = []
        for _ in range(MAX_INCIDENTS):
            svc = event["service"]
            outcome = heal(event, f"{svc}.main:app", rig.trees[svc],
                           complete=model, incident_id=f"live-{entry.id}",
                           suite_path=None, on_event=on_heal_event)
            if outcome.status != "deployed":
                refused = outcome.reason.startswith("reproduction refused")
                result["outcome"] = "refused" if refused else "escalated"
                send("handed_off", reason=outcome.reason, refused=refused)
                break
            send("deploying", service=svc)
            rig.deploy_patch(outcome.verification_record, outcome.patched_tree,
                             service=svc)
            send("deployed", service=svc)
            if certificates is not None:
                cert = certificates.issue(event, outcome, f"{svc}.main:app")
                certs.append(cert)
                send("certificate", sequence=cert["sequence"], hash=cert["hash"],
                     signature=cert["signature"],
                     replays=cert["reproduction"]["reason"],
                     attempts=len(cert["candidates"]))
            healed_keys.add(_incident_key(event))
            rig.new_events()
            if rig.probe():
                passed_at = time.time()
                send("probe", passed=True)
                if rig.traffic_clean():
                    result["outcome"] = "recovered"
                    result["mttr_seconds"] = round(passed_at - first_error, 2)
                    send("recovered", mttr=result["mttr_seconds"])
                    break
            nxt = rig.wait_for_event(timeout=5.0)
            if nxt is None or _incident_key(nxt) in healed_keys:
                result["outcome"] = "false_fix"
                send("false_fix")
                break
            event = nxt
            send("captured", exception_type=event.get("exception_type"),
                 message=str(event.get("exception_message", ""))[:160],
                 method=event.get("request", {}).get("method"),
                 path=event.get("request", {}).get("path"),
                 service=event.get("service"), again=True)
        time.sleep(COOLDOWN_SECONDS)
        return result
    except Exception as exc:                          # shown, never hidden
        send("error", message=f"{type(exc).__name__}: {exc}"[:300])
        result["outcome"] = "error"
        return result
    finally:
        stop_pump.set()
        if pump_thread.is_alive():
            pump_thread.join(timeout=1)
        send("end", outcome=result["outcome"], mttr=result["mttr_seconds"])
        rig.stop()
        shutil.rmtree(workdir, ignore_errors=True)
