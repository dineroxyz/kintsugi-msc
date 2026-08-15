"""Phase 3 exit criteria, as tests.

The full loop heals a live-captured fault end to end: reproduce, diagnose,
gate, deploy — and the deployed tree serves the previously failing request.
The gate rejects each class of degenerate repair. The deploy controller
refuses anything that is not a gate-signed record. The metric guard promotes
healthy canaries and breaches unhealthy ones.

The diagnosis model here is scripted: phase 3 proves the machinery around
the model, which is the thesis; the model's own contribution is what the
study measures.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient

from control_plane.deploy.controller import (DeployController, DeployRefused,
                                             GuardThresholds, WindowStats,
                                             guard_decision)
from control_plane.gate.verifier import gate, structural_check
from control_plane.loop import heal

SERVICE = textwrap.dedent('''
    from fastapi import FastAPI
    from libs.kintsugi_capture import CaptureMiddleware

    app = FastAPI()
    app.add_middleware(CaptureMiddleware, service="shop", release="s1")

    ORDERS = {1: {"id": 1, "total": 100.0}}

    @app.get("/orders/{oid}/discounted")
    def discounted(oid: int, code: str = ""):
        order = ORDERS[oid]
        rate = {"SAVE10": 0.10}.get(code)
        return {"id": oid, "discounted": round(order["total"] * (1 - rate), 2)}
''')

GOOD_DIFF = textwrap.dedent('''\
    --- a/shop_service/main.py
    +++ b/shop_service/main.py
    @@ -10,5 +10,5 @@
     @app.get("/orders/{oid}/discounted")
     def discounted(oid: int, code: str = ""):
         order = ORDERS[oid]
    -    rate = {"SAVE10": 0.10}.get(code)
    +    rate = {"SAVE10": 0.10}.get(code, 0.0)
         return {"id": oid, "discounted": round(order["total"] * (1 - rate), 2)}
''')

SWALLOW_DIFF = textwrap.dedent('''\
    --- a/shop_service/main.py
    +++ b/shop_service/main.py
    @@ -10,5 +10,8 @@
     @app.get("/orders/{oid}/discounted")
     def discounted(oid: int, code: str = ""):
    -    order = ORDERS[oid]
    -    rate = {"SAVE10": 0.10}.get(code)
    -    return {"id": oid, "discounted": round(order["total"] * (1 - rate), 2)}
    +    try:
    +        order = ORDERS[oid]
    +        rate = {"SAVE10": 0.10}.get(code)
    +        return {"id": oid, "discounted": round(order["total"] * (1 - rate), 2)}
    +    except TypeError:
    +        return {"id": oid, "discounted": None}
''')

WRONG_DIFF = textwrap.dedent('''\
    --- a/shop_service/main.py
    +++ b/shop_service/main.py
    @@ -10,5 +10,5 @@
     @app.get("/orders/{oid}/discounted")
     def discounted(oid: int, code: str = ""):
         order = ORDERS[oid]
    -    rate = {"SAVE10": 0.10}.get(code)
    +    rate = {"SAVE10": 0.10}.get(code)  # looked at it, seems fine
         return {"id": oid, "discounted": round(order["total"] * (1 - rate), 2)}
''')


@pytest.fixture()
def shop(tmp_path):
    """A live shop service in its own tree, with one captured incident."""
    root = tmp_path / "live"
    pkg = root / "shop_service"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text(SERVICE)
    sys.path.insert(0, str(root))

    events: list[dict] = []
    import importlib
    # purge any shop_service cached from a previous test's tree
    for name in [n for n in sys.modules if n.startswith("shop_service")]:
        del sys.modules[name]
    module = importlib.import_module("shop_service.main")
    from libs.kintsugi_capture import CaptureMiddleware
    module.app.user_middleware.clear()
    module.app.middleware_stack = None
    module.app.add_middleware(CaptureMiddleware, service="shop", release="s1",
                              deliver=events.append)
    client = TestClient(module.app, raise_server_exceptions=False)
    assert client.get("/orders/1/discounted?code=BOGUS").status_code == 500
    assert events

    yield {"root": str(root), "capture": events[-1],
           "spec": "shop_service.main:app"}
    sys.path.remove(str(root))


def scripted_model(*diffs):
    """A completion function that plays the given diffs in order."""
    queue = list(diffs)
    return lambda evidence: queue.pop(0)


# ------------------------------------------------------------------ the loop

def test_full_selfheal_end_to_end(shop):
    outcome = heal(shop["capture"], shop["spec"], shop["root"],
                   complete=scripted_model(GOOD_DIFF), incident_id="7",
                   suite_path=None)

    assert outcome.status == "deployed", outcome.reason
    assert outcome.signature.startswith("TypeError@")
    assert outcome.verification_record is not None
    assert outcome.regression_test and "test_incident_7_stays_fixed" in outcome.regression_test

    # deploy the verified tree over the live one and prove the request heals
    controller = DeployController(shop["root"])
    controller.deploy(outcome.verification_record, outcome.patched_tree)

    import importlib
    for name in [n for n in sys.modules if n.startswith("shop_service")]:
        del sys.modules[name]
    import shop_service.main as m
    client = TestClient(m.app, raise_server_exceptions=False)
    resp = client.get("/orders/1/discounted?code=BOGUS")
    assert resp.status_code == 200
    assert resp.json()["discounted"] == 100.0     # unknown code: no discount
    # the original behaviour still works
    assert client.get("/orders/1/discounted?code=SAVE10").json()["discounted"] == 90.0


def test_wrong_patch_is_rejected_then_retry_succeeds(shop):
    """A no-op patch passes structure but fails the reproduction; the loop
    feeds the failure back and admits the corrected second candidate."""
    outcome = heal(shop["capture"], shop["spec"], shop["root"],
                   complete=scripted_model(WRONG_DIFF, GOOD_DIFF),
                   suite_path=None)
    assert outcome.status == "deployed"
    assert len(outcome.candidates) == 2
    assert outcome.candidates[0]["conditions"]["reproduction"] is False
    assert outcome.candidates[1]["conditions"]["reproduction"] is True


def test_exception_swallowing_patch_is_rejected_structurally(shop):
    verdict = structural_check(SWALLOW_DIFF, "TypeError")
    assert not verdict["passed"]
    assert any("swallows" in p for p in verdict["problems"])

    outcome = heal(shop["capture"], shop["spec"], shop["root"],
                   complete=scripted_model(SWALLOW_DIFF, SWALLOW_DIFF, SWALLOW_DIFF),
                   suite_path=None)
    assert outcome.status == "escalated"
    assert "no candidate passed" in outcome.reason


def test_test_deleting_patch_is_rejected():
    diff = "--- a/tests/test_orders.py\n+++ b/tests/test_orders.py\n@@ -1,2 +1 @@\n-assert x\n"
    verdict = structural_check(diff, "TypeError")
    assert not verdict["passed"]
    assert any("test file" in p for p in verdict["problems"])


def test_unreproduced_incident_escalates_not_repairs(shop):
    """Determinism refusal must stop the loop before any model call."""
    calls = {"n": 0}

    def counting_model(evidence):
        calls["n"] += 1
        return GOOD_DIFF

    broken = dict(shop["capture"], external_calls=[
        {"method": "GET", "url": "http://gone/x", "request_headers": {},
         "request_body": "", "status": 200, "response_headers": {},
         "response_body": "{}"}])
    # a capture whose replay makes no such call -> stubs unused -> fine;
    # instead break determinism by pointing at a request that succeeds
    healthy = dict(shop["capture"])
    healthy["request"] = dict(healthy["request"], path="/orders/1/discounted",
                              query="code=SAVE10")
    outcome = heal(healthy, shop["spec"], shop["root"],
                   complete=counting_model, suite_path=None)
    assert outcome.status == "escalated"
    assert "did not recur" in outcome.reason
    assert calls["n"] == 0, "model was consulted despite refusal"


# ------------------------------------------------------- controller and guard

def test_deploy_controller_refuses_unsigned_records(shop, tmp_path):
    controller = DeployController(shop["root"])
    with pytest.raises(DeployRefused):
        controller.deploy(None, str(tmp_path))
    forged = json.dumps({"payload": {"signature": "x", "conditions": {}},
                         "hmac": "0" * 64})
    with pytest.raises(DeployRefused):
        controller.deploy(forged, str(tmp_path))


def test_rollback_restores_predeploy_tree(shop):
    outcome = heal(shop["capture"], shop["spec"], shop["root"],
                   complete=scripted_model(GOOD_DIFF), suite_path=None)
    controller = DeployController(shop["root"])
    before = open(os.path.join(shop["root"], "shop_service/main.py")).read()
    controller.deploy(outcome.verification_record, outcome.patched_tree)
    after = open(os.path.join(shop["root"], "shop_service/main.py")).read()
    assert before != after
    controller.rollback()
    restored = open(os.path.join(shop["root"], "shop_service/main.py")).read()
    assert restored == before


def test_metric_guard_promotes_and_breaches():
    base = WindowStats(requests=1000, errors=10, p95_latency_ms=120)
    good = WindowStats(requests=980, errors=11, p95_latency_ms=130)
    assert guard_decision(base, good)["promote"]

    error_spike = WindowStats(requests=1000, errors=60, p95_latency_ms=125)
    d = guard_decision(base, error_spike)
    assert not d["promote"] and "error rate" in d["breaches"][0]

    slow = WindowStats(requests=1000, errors=10, p95_latency_ms=400)
    d = guard_decision(base, slow)
    assert not d["promote"] and "latency" in d["breaches"][0]

    starved = WindowStats(requests=300, errors=3, p95_latency_ms=120)
    d = guard_decision(base, starved)
    assert not d["promote"] and "throughput" in d["breaches"][0]
