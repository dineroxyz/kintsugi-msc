"""The Live view's engine: run a real heal, or replay a recorded one.

Live runs need the whole platform and a model key, so they are switched on
only where both exist (KINTSUGI_LIVE=1, normally the presenter's laptop).
Everywhere else the Live view replays recordings of real runs, labelled as
such, through exactly the same screen. Every live run is recorded, so the
replays are never staged: they are earlier live runs played back at the
pace they happened.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDINGS = os.environ.get("KINTSUGI_LIVE_RECORDINGS",
                            os.path.join(ROOT, "demo", "recordings", "live"))
LIVE_CERTS = os.environ.get("KINTSUGI_LIVE_CERTS",
                            os.path.join(ROOT, "study", "live", "certificates"))


def enabled() -> bool:
    return os.environ.get("KINTSUGI_LIVE") == "1"


class Run:
    def __init__(self, entry_id: str):
        self.id = uuid.uuid4().hex[:12]
        self.entry_id = entry_id
        self.events: list[dict] = []
        self.done = False
        self.cond = threading.Condition()
        self.started = time.time()

    def add(self, event: dict) -> None:
        with self.cond:
            self.events.append(event)
            if event.get("kind") == "end":
                self.done = True
            self.cond.notify_all()

    def wait_from(self, index: int, timeout: float = 15.0) -> list[dict]:
        with self.cond:
            if index >= len(self.events) and not self.done:
                self.cond.wait(timeout)
            return self.events[index:]


_lock = threading.Lock()
_runs: dict[str, Run] = {}
_current: list[Run | None] = [None]


def busy() -> bool:
    run = _current[0]
    return run is not None and not run.done


def start(entry_id: str, *, complete=None) -> Run:
    """Start a live run in the background; one at a time."""
    from control_plane.certificates.issuer import CertificateStore
    from control_plane.study.live import run_live
    from corpus import load_entry

    with _lock:
        if busy():
            raise RuntimeError("a run is already in progress")
        entry = load_entry(entry_id)
        run = Run(entry_id)
        _runs[run.id] = run
        _current[0] = run

    def work() -> None:
        try:
            run_live(entry, run.add, complete=complete,
                     certificates=CertificateStore(LIVE_CERTS))
        except Exception as exc:              # the screen must always end
            run.add({"t": time.time() - run.started, "kind": "error",
                     "message": str(exc)[:300]})
            run.add({"t": time.time() - run.started, "kind": "end",
                     "outcome": "error", "mttr": None})
        finally:
            save(run)

    threading.Thread(target=work, daemon=True).start()
    return run


def get(run_id: str) -> Run | None:
    return _runs.get(run_id)


# ------------------------------------------------------------ recordings

def save(run: Run) -> str | None:
    if not run.events or run.events[-1].get("kind") != "end":
        return None
    end = run.events[-1]
    os.makedirs(RECORDINGS, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(run.started))
    path = os.path.join(RECORDINGS, f"{stamp}-{run.entry_id}.jsonl")
    meta = {"kind": "meta", "entry": run.entry_id,
            "recorded": time.strftime("%-d %B %Y", time.localtime(run.started)),
            "outcome": end.get("outcome"), "mttr": end.get("mttr"),
            "model": os.environ.get("KINTSUGI_MODEL", "claude-opus-5")}
    with open(path, "w") as fh:
        fh.write(json.dumps(meta) + "\n")
        for e in run.events:
            fh.write(json.dumps(e) + "\n")
    return path


def recordings() -> list[dict]:
    """Saved runs, newest first, each with its metadata line."""
    out = []
    if not os.path.isdir(RECORDINGS):
        return out
    for name in sorted(os.listdir(RECORDINGS), reverse=True):
        if not name.endswith(".jsonl"):
            continue
        try:
            with open(os.path.join(RECORDINGS, name)) as fh:
                meta = json.loads(fh.readline())
        except Exception:
            continue
        if meta.get("kind") == "meta" and meta.get("outcome") != "error":
            out.append({"name": name[:-6], **meta})
    return out


def load_recording(name: str) -> tuple[dict, list[dict]] | None:
    if not name or "/" in name or ".." in name:
        return None
    path = os.path.join(RECORDINGS, name + ".jsonl")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return rows[0], rows[1:]


def repair_count() -> int:
    """Repairs mended in gold: the study's certificates plus live ones."""
    total = 0
    for d in (os.environ.get("KINTSUGI_CERT_DIR", ""), LIVE_CERTS):
        if d and os.path.isdir(d):
            total += sum(1 for n in os.listdir(d) if n.endswith(".json"))
    return total
