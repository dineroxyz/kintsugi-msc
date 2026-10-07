"""Public-key signatures for certificates (Ed25519).

The chain's original signature is an HMAC: it proves a certificate came from
whoever holds the shared key, so only that holder can check it. Ed25519
fixes that: Kintsugi signs with a private key and publishes the public key,
and anyone can verify, in a browser if they like, without trusting the
server or holding a secret.

Two uses:
  * new certificates carry an "ed25519" block: the public key and a
    signature over the certificate's hash;
  * an existing chain (such as the main study's 494 certificates, issued
    before this key existed) gets an attestation: a signed, dated list of
    every certificate hash in it. The attestation says plainly when it was
    made; it vouches for the chain from that moment on, not before.

The private key is a 32-byte seed in KINTSUGI_ED25519_SEED (base64). It is
never written to disk by this module and never printed.
"""

from __future__ import annotations

import base64
import json
import os
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def private_key() -> Ed25519PrivateKey | None:
    seed = os.environ.get("KINTSUGI_ED25519_SEED")
    if not seed:
        return None
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed))


def public_key_b64() -> str | None:
    key = private_key()
    if key is None:
        return None
    return _b64(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))


def key_id(pub_b64: str) -> str:
    """A short, human-checkable fingerprint of a public key."""
    import hashlib
    return hashlib.sha256(base64.b64decode(pub_b64)).hexdigest()[:16]


def sign_hash(cert_hash: str) -> dict | None:
    """The block added to a new certificate: who signed, and the signature
    over the certificate's hex hash (as ASCII bytes)."""
    key = private_key()
    if key is None:
        return None
    pub = public_key_b64()
    return {"alg": "Ed25519", "key": pub, "key_id": key_id(pub),
            "sig": _b64(key.sign(cert_hash.encode()))}


def verify(pub_b64: str, message: bytes, sig_b64: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64)).verify(
            base64.b64decode(sig_b64), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def attestation_payload(chain: str, hashes: list[str], at: str) -> bytes:
    return json.dumps({"chain": chain, "hashes": hashes, "at": at},
                      sort_keys=True, separators=(",", ":")).encode()


def attest(chain: str, hashes: list[str]) -> dict:
    """Sign a dated list of a chain's certificate hashes."""
    key = private_key()
    if key is None:
        raise RuntimeError("no signing key (KINTSUGI_ED25519_SEED) configured")
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    pub = public_key_b64()
    payload = attestation_payload(chain, hashes, at)
    return {"chain": chain, "at": at, "count": len(hashes), "hashes": hashes,
            "alg": "Ed25519", "key": pub, "key_id": key_id(pub),
            "sig": _b64(key.sign(payload)),
            "note": ("Signed when this key was created. It vouches that these "
                     "certificates exist unchanged from this date on; the "
                     "certificates themselves were issued earlier under the "
                     "chain's original keyed-hash signature.")}


def verify_attestation(att: dict) -> bool:
    payload = attestation_payload(att["chain"], att["hashes"], att["at"])
    return verify(att["key"], payload, att["sig"])
