"""Corpus exit criteria, per entry: the fault injects into a clean tree,
fires under its trigger with the expected exception, and the ground-truth
fix silences it. The diffs the injector emits must also round-trip.

Beta runs as a real subprocess so alpha's outbound pricing calls are real
HTTP, exactly as they will be in the live study runs.
"""

from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient

from corpus import load_corpus
from corpus.injector import apply_true_fix, fault_diff, fix_diff, inject
from control_plane.deploy.live import ServiceProcess, free_port
from libs.kintsugi_capture import CaptureMiddleware

PLATFORM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES = os.path.join(PLATFORM_ROOT, "services")

CORPUS = load_corpus()
MAX_TRIGGER_ATTEMPTS = 30   # nondeterministic faults may need a few shots


@pytest.fixture(scope="module")
def beta():
    proc = ServiceProcess(SERVICES, "beta.main:app", free_port(), "b1",
                          PLATFORM_ROOT)
    proc.start()
    os.environ["BETA_URL"] = f"http://127.0.0.1:{proc.port}"
    yield proc
    proc.stop()


def _client(tree: str, events: list[dict]) -> TestClient:
    """Import alpha from `tree` and wire the capture agent to `events`."""
    sys.path.insert(0, tree)
    for name in [n for n in sys.modules if n.startswith("alpha")]:
        del sys.modules[name]
    try:
        module = importlib.import_module("alpha.main")
    finally:
        sys.path.remove(tree)
    module.app.user_middleware.clear()
    module.app.middleware_stack = None
    module.app.add_middleware(CaptureMiddleware, service="alpha", release="r1",
                              deliver=events.append)
    return TestClient(module.app, raise_server_exceptions=False)


def _run(client, request: dict):
    return client.request(request["method"], request["path"],
                          params=request.get("params"),
                          json=request.get("json"))


@pytest.mark.parametrize("entry", CORPUS, ids=[e.id for e in CORPUS])
def test_fault_fires_and_true_fix_silences(entry, beta, tmp_path):
    faulty = inject(entry, SERVICES, str(tmp_path / "faulty"))

    events: list[dict] = []
    client = _client(faulty, events)
    for request in entry.setup:
        assert _run(client, request).status_code < 400, \
            f"{entry.id}: setup request failed"

    fired = False
    for _ in range(MAX_TRIGGER_ATTEMPTS if not entry.deterministic else 1):
        if _run(client, entry.trigger).status_code == 500:
            fired = True
            break
    assert fired, f"{entry.id}: trigger never produced a 500"
    assert events, f"{entry.id}: capture agent recorded nothing"
    assert events[-1]["exception_type"] == entry.expected_exception

    # the ground-truth fix, applied to the faulty tree, silences the fault
    apply_true_fix(entry, faulty)
    events.clear()
    client = _client(faulty, events)
    for request in entry.setup:
        assert _run(client, request).status_code < 400
    attempts = 1 if entry.deterministic else MAX_TRIGGER_ATTEMPTS
    for _ in range(attempts):
        assert _run(client, entry.trigger).status_code != 500, \
            f"{entry.id}: fault still fires after the true fix"


@pytest.mark.parametrize("entry", CORPUS, ids=[e.id for e in CORPUS])
def test_diffs_are_emitted_both_ways(entry, tmp_path):
    forward = fault_diff(entry, SERVICES)
    assert forward.strip(), f"{entry.id}: empty fault diff"
    assert all(f"a/{m.file}" in forward for m in entry.mutations)

    faulty = inject(entry, SERVICES, str(tmp_path / "faulty"))
    backward = fix_diff(entry, faulty)
    assert backward.strip(), f"{entry.id}: empty fix diff"


def test_corpus_shape():
    assert len(CORPUS) >= 10
    assert sum(1 for e in CORPUS if e.expected_c3 == "refused") >= 3
    assert sum(1 for e in CORPUS if not e.deterministic) >= 1
    assert all(e.provenance.get("origin") for e in CORPUS)
    assert all(e.expected_exception for e in CORPUS)
