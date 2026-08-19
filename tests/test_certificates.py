"""Repair certificates: issuance, the hash chain, tamper evidence, and
replay-from-certificate — the 4b exit criterion that any historical fix
replays from its certificate alone.
"""

from __future__ import annotations

import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient

from control_plane.certificates.issuer import (CertificateError,
                                               CertificateStore,
                                               replay_certificate)
from control_plane.deploy.live import ServiceProcess, free_port
from control_plane.loop import heal
from corpus import load_entry
from corpus.injector import fix_diff, inject
from libs.kintsugi_capture import CaptureMiddleware

PLATFORM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES = os.path.join(PLATFORM_ROOT, "services")


@pytest.fixture(scope="module")
def clean_beta():
    proc = ServiceProcess(SERVICES, "beta.main:app", free_port(), "b1",
                          PLATFORM_ROOT)
    proc.start()
    os.environ["BETA_URL"] = f"http://127.0.0.1:{proc.port}"
    yield proc
    proc.stop()


def capture_incident(entry, tree: str) -> dict:
    """Fire the entry's trigger in-process and return the capture event."""
    sys.path.insert(0, tree)
    for name in [n for n in sys.modules if n.startswith(("alpha", "beta"))]:
        del sys.modules[name]
    try:
        module = importlib.import_module(f"{entry.trigger_service}.main")
    finally:
        sys.path.remove(tree)
    events: list[dict] = []
    module.app.user_middleware.clear()
    module.app.middleware_stack = None
    module.app.add_middleware(CaptureMiddleware,
                              service=entry.trigger_service, release="r2",
                              deliver=events.append)
    client = TestClient(module.app, raise_server_exceptions=False)
    for request in entry.setup:
        client.request(request["method"], request["path"],
                       params=request.get("params"),
                       json=request.get("json"))
    assert client.request(entry.trigger["method"], entry.trigger["path"],
                          params=entry.trigger.get("params"),
                          json=entry.trigger.get("json")).status_code == 500
    return events[-1]


def healed_outcome(entry_id: str, tmp_path, tag: str):
    entry = load_entry(entry_id)
    faulty = inject(entry, SERVICES, str(tmp_path / f"faulty-{tag}"))
    capture = capture_incident(entry, faulty)
    diff = fix_diff(entry, faulty)
    outcome = heal(capture, f"{entry.trigger_service}.main:app", faulty,
                   complete=lambda _e: diff, suite_path=None)
    assert outcome.status == "deployed"
    return entry, capture, outcome


def test_chain_grows_verifies_and_replays(clean_beta, tmp_path):
    store = CertificateStore(str(tmp_path / "certs"))
    entry, capture, outcome = healed_outcome("F01", tmp_path, "one")
    cert1 = store.issue(capture, outcome, "alpha.main:app")
    _, capture2, outcome2 = healed_outcome("F12", tmp_path, "two")
    cert2 = store.issue(capture2, outcome2, "alpha.main:app")

    chain = store.verify_chain()
    assert [c["sequence"] for c in chain] == [0, 1]
    assert cert2["prev_hash"] == cert1["hash"]

    # the exit criterion: the fix replays from the certificate alone,
    # against a freshly injected faulty tree the certificate never saw
    fresh_faulty = inject(entry, SERVICES, str(tmp_path / "fresh"))
    verdict = replay_certificate(cert1, fresh_faulty)
    assert verdict["reproduces"], verdict
    assert verdict["patch_applies"]
    assert verdict["patch_heals"], verdict


def test_tampering_breaks_the_chain(clean_beta, tmp_path):
    store = CertificateStore(str(tmp_path / "certs"))
    _, capture, outcome = healed_outcome("F12", tmp_path, "t")
    store.issue(capture, outcome, "alpha.main:app")

    path = os.path.join(store.root, "000000.json")
    cert = json.load(open(path))
    cert["patch"] = cert["patch"].replace("ORDERS", "0RDERS")
    json.dump(cert, open(path, "w"))

    with pytest.raises(CertificateError, match="tampered"):
        store.verify_chain()


def test_forged_hmac_is_refused(clean_beta, tmp_path):
    store = CertificateStore(str(tmp_path / "certs"))
    _, capture, outcome = healed_outcome("F12", tmp_path, "f")
    cert = store.issue(capture, outcome, "alpha.main:app")

    cert["hmac"] = "0" * 64
    with pytest.raises(CertificateError, match="forged"):
        replay_certificate(cert, SERVICES)

    path = os.path.join(store.root, "000000.json")
    json.dump(cert, open(path, "w"))
    with pytest.raises(CertificateError, match="forged"):
        store.verify_chain()


def test_only_deployed_outcomes_are_certifiable(clean_beta, tmp_path):
    entry = load_entry("F02")     # stateful: the gate refuses it
    faulty = inject(entry, SERVICES, str(tmp_path / "faulty-r"))
    capture = capture_incident(entry, faulty)
    outcome = heal(capture, "alpha.main:app", faulty,
                   complete=lambda _e: "", suite_path=None)
    assert outcome.status == "escalated"

    store = CertificateStore(str(tmp_path / "certs"))
    with pytest.raises(CertificateError, match="deployed"):
        store.issue(capture, outcome, "alpha.main:app")
