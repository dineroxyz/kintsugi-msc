"""Viva demo segments, each a real run with narrated output.

    python demo/segments.py heal       # live break-and-heal with the real model
    python demo/segments.py replay     # check certificates and replay one
    python demo/segments.py refusal    # a race the platform will not touch
    python demo/segments.py all

Everything printed comes from the run itself: the fault, the platform's
decisions, the timings and the certificate. Nothing is scripted except the
order of the explanations. demo/record.py records these into videos.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite://")

# model credentials and the study's signing key, never printed
for line in open(os.path.join(ROOT, ".env")):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
_KEY = os.path.join(ROOT, "study", "main", "STUDY_GATE_KEY")
if os.path.exists(_KEY):
    os.environ["KINTSUGI_GATE_KEY"] = open(_KEY).read().strip()

from corpus import load_entry  # noqa: E402

MAIN = os.path.join(ROOT, "study", "main")
WIDTH = 72


def say(text: str = "", pause: float = 0.0) -> None:
    print(text, flush=True)
    if pause:
        time.sleep(pause)


def rule(title: str) -> None:
    say()
    say(f"== {title} " + "=" * max(0, WIDTH - len(title) - 4), 0.6)


def show_timeline(trial) -> None:
    tm = trial.timings
    start = tm.get("fault_deployed")
    steps = [("fault_deployed", "the bug goes live"),
             ("first_error", "first error in live traffic"),
             ("heal_finished", "a fix passes every check"),
             ("patch_deployed", "the fix is live"),
             ("recovered", "the failing request works again")]
    for key, label in steps:
        if start is not None and key in tm:
            say(f"  {tm[key] - start:6.2f} s   {label}", 0.35)


# ------------------------------------------------------------------ heal

def heal() -> None:
    from control_plane.certificates.issuer import CertificateStore
    from control_plane.study.runner import run_trial

    entry = load_entry("F03")
    rule("Live break and heal")
    say(f"Bug {entry.id}: {entry.title}", 0.5)
    say(f"Service: {entry.service}. Trigger: {entry.trigger['method']} "
        f"{entry.trigger['path']}", 0.5)
    say()
    say("Starting the clean service under live load, then deploying the bug", 0.3)
    say("over it. Kintsugi will capture the failure, replay it in a shadow,", 0.3)
    say("ask the model for a fix, and only deploy a fix that passes the gate.", 0.8)
    say()
    say("Running...", 0.2)
    store_dir = tempfile.mkdtemp(prefix="demo-certs-")
    work = tempfile.mkdtemp(prefix="demo-heal-")
    t0 = time.time()
    try:
        trial = run_trial(entry, "C3", work,
                          certificates=CertificateStore(store_dir))
        elapsed = time.time() - t0
        say(f"Done in {elapsed:.1f} s.", 0.6)
        say()
        say("Timeline", 0.3)
        show_timeline(trial)
        say()
        d = trial.detail
        say(f"Outcome: {trial.outcome}", 0.3)
        if trial.mttr_seconds is not None:
            say(f"Time to recovery: {trial.mttr_seconds:.2f} s "
                "(first error to working request)", 0.3)
        say(f"Patches the model proposed: {d.get('candidates')}", 0.3)
        say(f"Gate decision: {d.get('heal_reason')}", 0.6)
        certs = CertificateStore(store_dir).all()
        if certs:
            c = certs[-1]
            say()
            say("Certificate issued", 0.3)
            say(f"  id        {c['hash'][:32]}", 0.2)
            say(f"  signature {c['signature']}", 0.2)
            say(f"  replays   {c['reproduction']['reason']}", 0.2)
            for cand in c["candidates"]:
                conds = cand["conditions"]
                verdict = "admitted" if conds and all(conds.values()) else "rejected"
                failed = [k for k, v in conds.items() if not v]
                why = f" (failed: {', '.join(failed)})" if failed else ""
                say(f"  attempt {cand['attempt']}: {verdict}{why}", 0.3)
            say()
            say("The admitted patch", 0.3)
            for line in c["patch"].splitlines():
                say("  " + line, 0.06)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


# ---------------------------------------------------------------- replay

def replay() -> None:
    from control_plane.certificates.issuer import (CertificateError,
                                                   CertificateStore,
                                                   replay_certificate)
    from corpus.injector import inject

    rule("Checking the certificates")
    store = CertificateStore(os.path.join(MAIN, "certificates"))
    chain = store.verify_chain()
    say(f"Main study chain: {len(chain)} certificates.", 0.4)
    say("Every certificate matches its own hash, links to the one before it,", 0.2)
    say("and carries a valid signature.", 0.8)

    seq = int(os.environ.get("DEMO_CERT", "0"))
    cert = next(c for c in chain if c["sequence"] == seq)
    entry_id = None
    for line in open(os.path.join(MAIN, "trials.jsonl")):
        t = json.loads(line)
        if cert["hash"] in t.get("detail", {}).get("certificates", []):
            entry_id = t["entry"]
            break
    entry = load_entry(entry_id)
    say()
    say(f"Certificate {seq:06d}: bug {entry.id}, {entry.title}", 0.6)
    say("Replaying it from the certificate alone, against a fresh copy of", 0.2)
    say("the broken service...", 0.4)
    work = tempfile.mkdtemp(prefix="demo-replay-")
    try:
        tree = inject(entry, os.path.join(ROOT, "services"),
                      os.path.join(work, "tree"))
        result = replay_certificate(cert, tree)
        say(f"  before the patch: failure reproduced   {result['reproduces']}", 0.4)
        say(f"  patch applies to the code              {result['patch_applies']}", 0.4)
        say(f"  after the patch: failure is gone        {result['patch_heals']}", 0.8)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    rule("Tampering is detected")
    say("Copying the chain and changing one character in one certificate...", 0.6)
    copy = tempfile.mkdtemp(prefix="demo-tamper-")
    try:
        shutil.copytree(os.path.join(MAIN, "certificates"),
                        os.path.join(copy, "c"))
        path = os.path.join(copy, "c", "000100.json")
        c = json.load(open(path))
        i = len(c["patch"]) // 2             # flip one real character
        c["patch"] = c["patch"][:i] + ("x" if c["patch"][i] != "x" else "y") + c["patch"][i + 1:]
        json.dump(c, open(path, "w"))
        try:
            CertificateStore(os.path.join(copy, "c")).verify_chain()
            say("  NOT DETECTED: this would be a defect", 0.6)
        except CertificateError as exc:
            say(f"  {exc}", 0.8)
    finally:
        shutil.rmtree(copy, ignore_errors=True)


# --------------------------------------------------------------- refusal

def refusal() -> None:
    from control_plane.study.runner import run_trial

    entry = load_entry("F11")
    rule("A bug Kintsugi will not touch")
    say(f"Bug {entry.id}: {entry.title}", 0.5)
    say("This failure only happens when two requests collide. It cannot be", 0.2)
    say("made to happen on demand, so no fix can be tested against it.", 0.8)
    say()
    say("Running...", 0.2)
    work = tempfile.mkdtemp(prefix="demo-refusal-")
    t0 = time.time()
    try:
        trial = run_trial(entry, "C3", work)
        say(f"Done in {time.time() - t0:.1f} s.", 0.6)
        say()
        say(f"Outcome: {trial.outcome}", 0.4)
        say(f"Reason:  {trial.detail.get('heal_reason')}", 0.4)
        say(f"Model calls made: {trial.detail.get('candidates', 0)}", 0.6)
        say()
        say("No patch was written and nothing was deployed. The incident is", 0.2)
        say("handed to a person with the reason attached.", 0.6)
    finally:
        shutil.rmtree(work, ignore_errors=True)


SEGMENTS = {"heal": heal, "replay": replay, "refusal": refusal}

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    for name in (SEGMENTS if which == "all" else [which]):
        SEGMENTS[name]()
