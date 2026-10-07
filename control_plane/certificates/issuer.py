"""Repair certificates: every recovery issued as a tamper-evident,
replayable proof.

A certificate carries everything needed to re-derive the recovery it
attests: the captured incident (redacted at source), the reproduction
verdicts, every candidate the gate saw, the admitted patch, and the gate's
signed verification record. Certificates form a hash chain in an
append-only store: each embeds the hash of its predecessor, the whole body
is hashed canonically, and the hash is HMAC-signed with the gate's key.
Editing any byte of any historical certificate breaks the chain from that
point forward.

Replay-from-certificate needs only the certificate and the faulty tree:
the embedded capture must reproduce the failure there, and the embedded
patch must silence it. No trust in the platform's word is required.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from control_plane.certificates.pubsig import sign_hash, verify
from control_plane.gate.verifier import _apply_patch, _sign, verify_record
from control_plane.reproduction.engine import reproduce

GENESIS = "0" * 64


class CertificateError(RuntimeError):
    pass


# fields that sit outside the hashed body: the hash itself and its signatures
SIGNATURE_FIELDS = ("hash", "hmac", "ed25519")


def body_of(cert: dict) -> dict:
    return {k: v for k, v in cert.items() if k not in SIGNATURE_FIELDS}


def canonical_bytes(cert: dict) -> bytes:
    """Exactly the bytes a certificate's hash is computed over."""
    return _canonical(body_of(cert))


def _canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


class CertificateStore:
    """Append-only chain of repair certificates on disk, one JSON per file,
    ordered by sequence number."""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    # -- issue -------------------------------------------------------------
    def issue(self, capture: dict, outcome, service_spec: str) -> dict:
        """Issue the certificate for one deployed recovery."""
        if outcome.status != "deployed":
            raise CertificateError(
                f"only deployed outcomes are certifiable, got {outcome.status}")
        admitted = outcome.candidates[-1]
        record = json.loads(outcome.verification_record)

        body = {
            "version": 1,
            "sequence": len(self._files()),
            "issued_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "service_spec": service_spec,
            "signature": outcome.signature,
            "capture": capture,
            "reproduction": outcome.reproduction,
            "candidates": outcome.candidates,
            "patch": admitted["diff"],
            "verification_record": record,
            "prev_hash": self._tip_hash(),
        }
        digest = hashlib.sha256(_canonical(body)).hexdigest()
        cert = {**body, "hash": digest, "hmac": _sign({"hash": digest})}
        public = sign_hash(digest)          # anyone can verify this one
        if public:
            cert["ed25519"] = public

        path = os.path.join(self.root, f"{body['sequence']:06d}.json")
        if os.path.exists(path):
            raise CertificateError(f"sequence collision at {path}")
        with open(path, "w") as fh:
            json.dump(cert, fh, indent=2)
        return cert

    # -- read and verify ---------------------------------------------------
    def _files(self) -> list[str]:
        return sorted(name for name in os.listdir(self.root)
                      if name.endswith(".json"))

    def _tip_hash(self) -> str:
        files = self._files()
        if not files:
            return GENESIS
        with open(os.path.join(self.root, files[-1])) as fh:
            return json.load(fh)["hash"]

    def all(self) -> list[dict]:
        certs = []
        for name in self._files():
            with open(os.path.join(self.root, name)) as fh:
                certs.append(json.load(fh))
        return certs

    def verify_chain(self) -> list[dict]:
        """Every certificate re-hashed, every link checked, every HMAC
        verified. Returns the chain or raises naming the broken link."""
        prev = GENESIS
        chain = []
        for cert in self.all():
            body = body_of(cert)
            digest = hashlib.sha256(_canonical(body)).hexdigest()
            if digest != cert["hash"]:
                raise CertificateError(
                    f"certificate {cert['sequence']} content does not match "
                    "its hash: tampered")
            if cert["prev_hash"] != prev:
                raise CertificateError(
                    f"certificate {cert['sequence']} does not chain to its "
                    "predecessor: chain broken")
            if not verify_record({"hash": cert["hash"]}, cert["hmac"]):
                raise CertificateError(
                    f"certificate {cert['sequence']} HMAC invalid: forged")
            pub = cert.get("ed25519")
            if pub and not verify(pub["key"], cert["hash"].encode(), pub["sig"]):
                raise CertificateError(
                    f"certificate {cert['sequence']} public signature invalid: forged")
            prev = cert["hash"]
            chain.append(cert)
        return chain


def replay_certificate(cert: dict, faulty_tree: str) -> dict:
    """Prove a historical repair from its certificate alone.

    Two obligations against the pre-patch tree the caller provides:
    the embedded capture reproduces the captured failure there, and the
    embedded patch silences it. Both use the same engine the original
    recovery used; nothing is taken from the certificate's own claims.
    """
    if not verify_record({"hash": cert["hash"]}, cert["hmac"]):
        raise CertificateError("refusing to replay a forged certificate")

    spec = cert["service_spec"]
    before = reproduce(cert["capture"], spec, workdir=faulty_tree)

    patched_tree, error = _apply_patch(faulty_tree, cert["patch"])
    if patched_tree is None:
        return {"reproduces": before.reproduced, "patch_applies": False,
                "patch_heals": False, "error": error}
    after = reproduce(cert["capture"], spec, workdir=patched_tree)

    return {"reproduces": before.reproduced,
            "patch_applies": True,
            "patch_heals": not after.reproduced,
            "before": {"status": before.status, "reason": before.reason},
            "after": {"status": after.status, "reason": after.reason}}
