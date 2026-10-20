"""Undo and redo a live repair on the still-running shop.

After a live run mends a bug, its shop is kept running for a short while.
Undo puts the faulty release back (the bug returns and the traffic shows
it). Redo puts the certified fix back without asking the model again: it
first re-checks the certificate (its hash, its place in the chain, its
signatures, and that its patch is the exact diff the gate signed), then
deploys that patch through the same deploy controller, which re-verifies
the gate's signed record before it will deploy anything.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time

from control_plane.deploy.controller import DeployController
from control_plane.gate.verifier import _apply_patch, verify_record
from corpus.injector import inject

KEEP_SECONDS = 180


class Session:
    def __init__(self, rig, entry, cert: dict, service: str, workdir: str, cert_dir: str):
        self.rig, self.entry, self.cert, self.service = rig, entry, cert, service
        self.workdir, self.cert_dir = workdir, cert_dir
        self.state = "mended"
        self.lock = threading.Lock()
        self.expires = time.time() + KEEP_SECONDS
        self.closed = False

    # -- shared plumbing ----------------------------------------------------
    def _stream(self, emit):
        t0 = time.time()
        stop = threading.Event()

        def send(kind, **data):
            emit({"t": round(time.time() - t0, 3), "kind": kind, **data})

        def pump():
            seen = len(self.rig.load.samples)
            while not stop.is_set():
                with self.rig.load._lock:
                    fresh = self.rig.load.samples[seen:]
                    seen = len(self.rig.load.samples)
                if fresh:
                    send("traffic", samples=[[round(s.t - t0, 3), s.status, s.path] for s in fresh])
                stop.wait(0.12)
        th = threading.Thread(target=pump, daemon=True)
        th.start()
        return t0, send, stop, th

    def _faulty_tree(self) -> str:
        path = os.path.join(self.workdir, "r2")
        if not os.path.exists(path):
            inject(self.entry, os.path.join(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))), "services"), path)
        return path

    # -- undo ----------------------------------------------------------------
    def undo(self, emit) -> None:
        with self.lock:
            t0, send, stop, th = self._stream(emit)
            try:
                send("undo_start", entry=self.entry.id, title=self.entry.title, service=self.service)
                target = self.rig.procs[self.entry.service]
                DeployController._sync(self._faulty_tree(), target.tree)
                target.release = "r2"
                target.restart()
                send("fault_deployed", service=self.entry.service, undo=True)
                deadline = time.time() + 6
                errors = 0
                while time.time() < deadline:
                    errors = len([s for s in self.rig.load.since(t0) if s.status >= 500])
                    if errors >= 5:
                        break
                    time.sleep(0.2)
                self.state = "broken"
                send("undo_done", errors=errors)
            except Exception as exc:
                send("error", message=f"{type(exc).__name__}: {exc}"[:300])
            finally:
                time.sleep(1.5)
                stop.set()
                th.join(timeout=1)
                self.expires = time.time() + KEEP_SECONDS
                send("end", outcome="undone" if self.state == "broken" else "error", mttr=None)

    # -- redo ----------------------------------------------------------------
    def check_certificate(self) -> dict:
        from control_plane.certificates.issuer import CertificateStore, canonical_bytes
        from control_plane.certificates.pubsig import verify
        cert = self.cert
        hash_ok = hashlib.sha256(canonical_bytes(cert)).hexdigest() == cert["hash"]
        try:
            CertificateStore(self.cert_dir).verify_chain()
            chain_ok = True
        except Exception:
            chain_ok = False
        record = cert["verification_record"]
        record_ok = verify_record(record["payload"], record["hmac"])
        patch_ok = hashlib.sha256(cert["patch"].encode()).hexdigest() == \
            record["payload"].get("diff_sha256")
        ed = cert.get("ed25519")
        ed_ok = bool(ed) and verify(ed["key"], cert["hash"].encode(), ed["sig"])
        return {"hash": hash_ok, "chain": chain_ok, "record": record_ok, "patch": patch_ok,
                "ed25519": ed_ok if ed else None}

    def redo(self, emit) -> None:
        with self.lock:
            t0, send, stop, th = self._stream(emit)
            try:
                send("redo_start", entry=self.entry.id, sequence=self.cert["sequence"])
                checks = self.check_certificate()
                send("cert_verified", sequence=self.cert["sequence"], cert_hash=self.cert["hash"], **checks)
                if not (checks["hash"] and checks["chain"] and checks["record"] and checks["patch"]):
                    send("redo_refused", reason="the certificate did not verify")
                    return
                tree, err = _apply_patch(self._faulty_tree(), self.cert["patch"])
                if tree is None:
                    send("redo_refused", reason=f"the certified patch no longer applies: {err[:120]}")
                    return
                send("deploying", service=self.service)
                self.rig.deploy_patch(json.dumps(self.cert["verification_record"]), tree,
                                      service=self.service)
                send("deployed", service=self.service)
                if self.rig.probe():
                    send("probe", passed=True)
                    if self.rig.traffic_clean():
                        self.state = "mended"
                        send("recovered", mttr=round(time.time() - t0, 2))
            except Exception as exc:
                send("error", message=f"{type(exc).__name__}: {exc}"[:300])
            finally:
                time.sleep(1.0)
                stop.set()
                th.join(timeout=1)
                self.expires = time.time() + KEEP_SECONDS
                send("end", outcome="redone" if self.state == "mended" else "error", mttr=None)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.rig.stop()
        finally:
            shutil.rmtree(self.workdir, ignore_errors=True)
