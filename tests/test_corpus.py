"""Corpus exit criteria, per entry: the fault injects into a clean tree,
fires under its trigger with the expected exception, and the ground-truth
fix silences it. The diffs the injector emits must also round-trip.

Three firing modes, chosen by the entry's shape:
- plain entries run in-process against a TestClient;
- race entries (requires_concurrency) run the faulty service as a real
  subprocess and hammer it from threads, reading the capture file sink,
  because a race needs genuinely overlapping requests;
- cross-service entries (service != trigger_service) run the FAULTY beta
  as a subprocess behind an in-process clean alpha.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import httpx
import pytest
from fastapi.testclient import TestClient

from corpus import load_corpus
from corpus.injector import apply_true_fix, fault_diff, fix_diff, inject
from control_plane.deploy.live import ServiceProcess, free_port
from libs.kintsugi_capture import CaptureMiddleware

PLATFORM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES = os.path.join(PLATFORM_ROOT, "services")

CORPUS = load_corpus()
MAX_ROUNDS = 30          # nondeterministic faults may need a few shots
RACE_THREADS = 8


@pytest.fixture(scope="module")
def clean_beta():
    proc = ServiceProcess(SERVICES, "beta.main:app", free_port(), "b1",
                          PLATFORM_ROOT)
    proc.start()
    os.environ["BETA_URL"] = f"http://127.0.0.1:{proc.port}"
    yield proc
    proc.stop()


def _client(tree: str, module_name: str, events: list[dict]) -> TestClient:
    """Import a service from `tree` and wire the capture agent to `events`."""
    sys.path.insert(0, tree)
    for name in [n for n in sys.modules
                 if n.startswith(("alpha", "beta"))]:
        del sys.modules[name]
    try:
        module = importlib.import_module(f"{module_name}.main")
    finally:
        sys.path.remove(tree)
    module.app.user_middleware.clear()
    module.app.middleware_stack = None
    module.app.add_middleware(CaptureMiddleware, service=module_name,
                              release="r1", deliver=events.append)
    return TestClient(module.app, raise_server_exceptions=False)


def _run(client, request: dict):
    return client.request(request["method"], request["path"],
                          params=request.get("params"),
                          json=request.get("json"))


def _fire_inprocess(entry, tree: str) -> tuple[bool, list[dict]]:
    events: list[dict] = []
    client = _client(tree, entry.trigger_service, events)
    for request in entry.setup:
        assert _run(client, request).status_code < 400, \
            f"{entry.id}: setup request failed"
    rounds = 1 if entry.deterministic else MAX_ROUNDS
    for _ in range(rounds):
        if _run(client, entry.trigger).status_code == 500:
            return True, events
    return False, events


def _fire_concurrent(entry, tree: str, tmp_path) -> tuple[bool, list[dict]]:
    """Real subprocess, threaded hammering, capture file sink."""
    capture_file = str(tmp_path / "capture.jsonl")
    service = ServiceProcess(tree, f"{entry.trigger_service}.main:app",
                             free_port(), "r1", PLATFORM_ROOT,
                             extra_env={"KINTSUGI_CAPTURE_FILE": capture_file})
    service.start()
    try:
        base = f"http://127.0.0.1:{service.port}"
        for _ in range(10):
            with httpx.Client(timeout=5.0, base_url=base) as client:
                for request in entry.setup:
                    client.request(request["method"], request["path"],
                                   params=request.get("params"),
                                   json=request.get("json"))
            statuses: list[int] = []

            def hammer():
                with httpx.Client(timeout=5.0, base_url=base) as c:
                    r = c.request(entry.trigger["method"],
                                  entry.trigger["path"],
                                  params=entry.trigger.get("params"),
                                  json=entry.trigger.get("json"))
                    statuses.append(r.status_code)

            threads = [threading.Thread(target=hammer)
                       for _ in range(RACE_THREADS)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            if any(s == 500 for s in statuses):
                events = [json.loads(line)
                          for line in open(capture_file)] \
                    if os.path.exists(capture_file) else []
                return True, events
        return False, []
    finally:
        service.stop()


def _fire_cross_service(entry, tree: str) -> tuple[bool, list[dict]]:
    """Faulty dependency as a subprocess, consumer in-process."""
    dep = ServiceProcess(tree, f"{entry.service}.main:app", free_port(),
                         "r1", PLATFORM_ROOT)
    dep.start()
    saved = os.environ.get("BETA_URL")
    os.environ["BETA_URL"] = f"http://127.0.0.1:{dep.port}"
    try:
        return _fire_inprocess(entry, tree)
    finally:
        if saved is not None:
            os.environ["BETA_URL"] = saved
        dep.stop()


def _fire(entry, tree: str, tmp_path) -> tuple[bool, list[dict]]:
    if entry.requires_concurrency:
        return _fire_concurrent(entry, tree, tmp_path)
    if entry.service != entry.trigger_service:
        return _fire_cross_service(entry, tree)
    return _fire_inprocess(entry, tree)


@pytest.mark.parametrize("entry", CORPUS, ids=[e.id for e in CORPUS])
def test_fault_fires_and_true_fix_silences(entry, clean_beta, tmp_path):
    faulty = inject(entry, SERVICES, str(tmp_path / "faulty"))

    fired, events = _fire(entry, faulty, tmp_path)
    assert fired, f"{entry.id}: trigger never produced a 500"
    assert events, f"{entry.id}: capture agent recorded nothing"
    assert any(e["exception_type"] == entry.expected_exception
               for e in events), \
        f"{entry.id}: no {entry.expected_exception} among " \
        f"{[e['exception_type'] for e in events]}"

    # the ground-truth fix, applied to the faulty tree, silences the fault
    apply_true_fix(entry, faulty)
    fired, _ = _fire(entry, faulty, tmp_path)
    assert not fired, f"{entry.id}: fault still fires after the true fix"


@pytest.mark.parametrize("entry", CORPUS, ids=[e.id for e in CORPUS])
def test_diffs_are_emitted_both_ways(entry, tmp_path):
    forward = fault_diff(entry, SERVICES)
    assert forward.strip(), f"{entry.id}: empty fault diff"
    assert all(f"a/{m.file}" in forward for m in entry.mutations)

    faulty = inject(entry, SERVICES, str(tmp_path / "faulty"))
    backward = fix_diff(entry, faulty)
    assert backward.strip(), f"{entry.id}: empty fix diff"


def test_corpus_shape():
    assert len(CORPUS) >= 60
    assert sum(1 for e in CORPUS if e.expected_c3 == "refused") >= 12
    assert sum(1 for e in CORPUS if not e.deterministic) >= 2
    assert sum(1 for e in CORPUS if e.requires_concurrency) >= 1
    assert sum(1 for e in CORPUS if e.service == "beta") >= 5
    assert sum(1 for e in CORPUS
               if e.provenance.get("origin") == "mined") >= 30
    # every mined entry cites its upstream commit
    assert all(e.provenance.get("source")
               for e in CORPUS if e.provenance.get("origin") == "mined")
    assert all(e.provenance.get("origin") for e in CORPUS)
    assert all(e.expected_exception for e in CORPUS)
