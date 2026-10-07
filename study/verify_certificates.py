"""Independent verification of the main study's certificate chain.

What a third party holding only the certificates and the corpus can check:

  1. integrity without the key: every certificate re-hashed from its own
     content and every link to its predecessor checked (SHA-256 only);
  2. authenticity with the key: the HMAC on every certificate;
  3. tamper detection: an edited patch, a removed certificate and a
     certificate signed under another key are each named as such;
  4. replay: for every certificate issued by the base gated arm, the
     embedded capture is replayed against a freshly injected faulty tree
     (it must fail), and again after applying the embedded patch (it must
     pass). Nothing is taken from the certificate's own claims.

Writes study/main/certificate_verification.json.

    python study/verify_certificates.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "study"))

import run_main  # noqa: E402,F401  (study gate key + environment)

from control_plane.certificates import issuer  # noqa: E402
from control_plane.certificates.issuer import (CertificateError,  # noqa: E402
                                               CertificateStore,
                                               replay_certificate)
from control_plane.metrics.hardening import SERVICES  # noqa: E402
from corpus import load_corpus  # noqa: E402
from corpus.injector import inject  # noqa: E402

MAIN = os.path.join(ROOT, "study", "main")
CERTS = os.path.join(MAIN, "certificates")


def keyless_integrity(certs: list[dict]) -> dict:
    prev = issuer.GENESIS
    for cert in certs:
        if hashlib.sha256(issuer.canonical_bytes(cert)).hexdigest() != cert["hash"]:
            return {"ok": False, "at": cert["sequence"], "why": "tampered"}
        if cert["prev_hash"] != prev:
            return {"ok": False, "at": cert["sequence"], "why": "chain broken"}
        prev = cert["hash"]
    return {"ok": True, "certificates": len(certs)}


def tamper_checks() -> dict:
    results = {}

    def attempt(name, mutate):
        tmp = tempfile.mkdtemp(prefix="cert-tamper-")
        try:
            store_dir = os.path.join(tmp, "c")
            shutil.copytree(CERTS, store_dir)
            mutate(store_dir)
            CertificateStore(store_dir).verify_chain()
            results[name] = "NOT DETECTED"
        except CertificateError as exc:
            results[name] = f"detected: {exc}"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def edit_patch(d):
        p = os.path.join(d, "000100.json")
        c = json.load(open(p))
        c["patch"] = c["patch"].replace("+", "+    pass  # injected\n+", 1)
        json.dump(c, open(p, "w"))

    def remove_one(d):
        os.remove(os.path.join(d, "000200.json"))

    def foreign_key(d):
        p = os.path.join(d, "000300.json")
        c = json.load(open(p))
        body = json.dumps({"hash": c["hash"]}, sort_keys=True).encode()
        c["hmac"] = hmac.new(b"someone-elses-key", body,
                             hashlib.sha256).hexdigest()
        json.dump(c, open(p, "w"))

    attempt("edited patch", edit_patch)
    attempt("certificate removed", remove_one)
    attempt("signed under another key", foreign_key)
    return results


def main() -> int:
    store = CertificateStore(CERTS)
    certs = store.all()
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "keyless_integrity": keyless_integrity(certs)}
    try:
        out["authenticated_chain"] = {"ok": True,
                                      "certificates": len(store.verify_chain())}
    except CertificateError as exc:
        out["authenticated_chain"] = {"ok": False, "error": str(exc)}
    out["tamper_checks"] = tamper_checks()
    print(json.dumps(out, indent=1), flush=True)

    # replay every certificate issued by the base gated arm
    by_hash = {c["hash"]: c for c in certs}
    corpus = {e.id: e for e in load_corpus()}
    targets = []
    for line in open(os.path.join(MAIN, "trials.jsonl")):
        t = json.loads(line)
        if t["arm"] == "C3" and not t["variant"]:
            for h in t["detail"].get("certificates", []):
                targets.append((t["entry"], t["seed"], h))

    replays, workroot = [], tempfile.mkdtemp(prefix="cert-replay-")
    try:
        for i, (entry_id, seed, h) in enumerate(targets, 1):
            tree = inject(corpus[entry_id], SERVICES,
                          os.path.join(workroot, f"{i}-{entry_id}"))
            try:
                r = replay_certificate(by_hash[h], tree)
            except Exception as exc:          # recorded, never hidden
                r = {"error": f"{type(exc).__name__}: {exc}"}
            ok = bool(r.get("reproduces") and r.get("patch_heals"))
            replays.append({"entry": entry_id, "seed": seed, "hash": h[:16],
                            "sequence": by_hash[h]["sequence"],
                            "verified": ok,
                            **{k: r.get(k) for k in ("reproduces",
                                                     "patch_applies",
                                                     "patch_heals", "error")}})
            shutil.rmtree(tree, ignore_errors=True)
            print(f"[{i}/{len(targets)}] {entry_id} s{seed}: "
                  f"{'verified' if ok else 'FAILED ' + str(r)[:120]}", flush=True)
    finally:
        shutil.rmtree(workroot, ignore_errors=True)

    out["replay"] = {"certificates": len(replays),
                     "verified": sum(r["verified"] for r in replays),
                     "failed": [r for r in replays if not r["verified"]],
                     "rows": replays}
    with open(os.path.join(MAIN, "certificate_verification.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"replay: {out['replay']['verified']}/{len(replays)} verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
