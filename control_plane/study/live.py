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
        last_heal: dict = {}
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
                last_heal.update(svc=svc, outcome=outcome, cert=cert, event=event)
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
                    note = _explain(last_heal) if complete is None else None
                    if note:
                        send("explanation", text=note,
                             model=os.environ.get("KINTSUGI_EXPLAIN_MODEL", EXPLAIN_MODEL))
                    last_heal["explanation"] = note
                    pr = _record_pull_request(entry, rig, last_heal, result,
                                              os.path.basename(workdir))
                    if pr:
                        send("pull_request", **pr)
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


EXPLAIN_MODEL = "claude-haiku-4-5-20251001"


def _explain(heal: dict) -> str | None:
    """A short plain-English note on what broke and why, written by a small
    model from the evidence. Shown labelled as unverified: the certificate,
    not this note, is the proof."""
    if not heal.get("cert") or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        cert, event = heal["cert"], heal["event"]
        req = event.get("request", {})
        prompt = (
            "A web service crashed and an automated system fixed it. Explain to a "
            "non-programmer, in at most three short sentences and plain English, what "
            "went wrong and what the fix changed. No jargon, no code, no lists.\n\n"
            f"Request: {req.get('method', '')} {req.get('path', '')}\n"
            f"Error: {event.get('exception_type', '')}: {str(event.get('exception_message', ''))[:200]}\n"
            f"Patch:\n{cert['patch'][:3000]}")
        msg = anthropic.Anthropic().messages.create(
            model=os.environ.get("KINTSUGI_EXPLAIN_MODEL", EXPLAIN_MODEL),
            max_tokens=220, messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        return text[:700] or None
    except Exception:
        return None


def _record_pull_request(entry, rig, heal: dict, result: dict, run: str) -> dict | None:
    """Open the review pull request for a completed repair, if configured.
    Never fails the run: a GitHub problem becomes an event, not an error."""
    from control_plane.integrations import github_pr
    if not github_pr.configured() or not heal.get("cert"):
        return None
    try:
        outcome, cert, event = heal["outcome"], heal["cert"], heal["event"]
        patched = [line[6:].strip() for line in outcome.candidates[-1]["diff"].splitlines()
                   if line.startswith("+++ b/")]
        faulty_tree = os.path.join(rig.workdir, "r2")
        buggy = {}
        for m in entry.mutations:
            buggy[m.file] = open(os.path.join(faulty_tree, m.file)).read()
        for path in patched:
            buggy.setdefault(path, open(os.path.join(faulty_tree, path)).read())
        fixed = {path: open(os.path.join(rig.trees[heal["svc"]], path)).read()
                 for path in patched}
        req = event.get("request", {})
        conditions = (outcome.candidates[-1].get("conditions") or {})
        names = {"structural": "not just hiding the error", "applies": "applies to the code",
                 "reproduction": "the failure no longer happens", "suite": "nothing else broke"}
        site = os.environ.get("KINTSUGI_PUBLIC_URL", "https://console-production-f3b9.up.railway.app")
        return github_pr.open_repair_pr(
            run=run.replace("kintsugi-live-", "")[:24], entry_id=entry.id, title=entry.title,
            buggy=buggy, fixed=fixed, regression_test=outcome.regression_test,
            evidence={"signature": cert["signature"], "request": f"{req.get('method', '')} {req.get('path', '')}",
                      "exception": event.get("exception_type", ""),
                      "replays": cert["reproduction"]["reason"],
                      "attempts": len(cert["candidates"]), "mttr": result["mttr_seconds"],
                      "conditions": {names.get(k, k): v for k, v in conditions.items()},
                      "certificate_url": f"{site}/live-certificates/{cert['sequence']}",
                      "certificate_hash": cert["hash"],
                      "model": os.environ.get("KINTSUGI_MODEL", "claude-opus-5"),
                      "explanation": heal.get("explanation")})
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}
