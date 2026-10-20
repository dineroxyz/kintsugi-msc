"""Phase 1 exit criteria, as tests.

1. An unhandled exception in a managed service lands in the control plane as
   a structured capture row with the provoking request and the outbound call
   record attached.
2. 500 identical failures form exactly one incident.
3. Two distinct faults form two incidents.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = "sqlite://"  # in-memory, per test process

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.api.main import app as control_plane_app
from control_plane.db import init_db
from libs.kintsugi_capture import CaptureClient, CaptureMiddleware


@pytest.fixture()
def control_plane():
    from control_plane.db import Base, engine
    Base.metadata.drop_all(engine)
    init_db()
    return TestClient(control_plane_app)


def make_service(control_plane: TestClient, release: str = "r1") -> TestClient:
    """A tiny managed service whose capture agent delivers into the
    control plane TestClient, in-process and synchronously."""
    app = FastAPI()

    app.add_middleware(
        CaptureMiddleware, service="testsvc", release=release,
        deliver=lambda event: control_plane.post("/ingest/event", json=event),
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        sku = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"sku": sku, "price": 9.5})

    external = CaptureClient(transport=httpx.MockTransport(upstream))

    @app.get("/boom")
    def boom():
        # outbound call happens first, so the capture must contain it
        external.get("http://beta/price/widget")
        raise KeyError("SAVE15")

    @app.get("/other")
    def other():
        raise ZeroDivisionError("division by zero")

    @app.get("/fine")
    def fine():
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


def test_exception_lands_with_request_and_external_calls(control_plane):
    svc = make_service(control_plane)
    resp = svc.get("/boom?x=1", headers={"Authorization": "Bearer sekrit"})
    assert resp.status_code == 500

    incidents = control_plane.get("/incidents").json()
    assert len(incidents) == 1
    detail = control_plane.get(f"/incidents/{incidents[0]['id']}").json()
    cap = detail["sample_captures"][0]

    assert detail["exception_type"] == "KeyError"
    assert cap["request"]["path"] == "/boom"
    assert cap["request"]["query"] == "x=1"
    # secrets never reach the store
    assert cap["request"]["headers"]["authorization"] == "[redacted]"
    # the outbound call was recorded with its response, ready for hermetic replay
    assert len(cap["external_calls"]) == 1
    call = cap["external_calls"][0]
    assert call["url"].endswith("/price/widget")
    assert call["status"] == 200
    assert "9.5" in call["response_body"]
    # frames identify the failing handler
    assert any(f["function"] == "boom" for f in cap["frames"])


def test_500_identical_failures_form_one_incident(control_plane):
    svc = make_service(control_plane)
    for _ in range(500):
        svc.get("/boom")
    incidents = control_plane.get("/incidents").json()
    assert len(incidents) == 1
    assert incidents[0]["capture_count"] == 500


def test_two_distinct_faults_form_two_incidents(control_plane):
    svc = make_service(control_plane)
    for _ in range(3):
        svc.get("/boom")
    for _ in range(3):
        svc.get("/other")
    incidents = control_plane.get("/incidents").json()
    assert len(incidents) == 2
    types = {i["exception_type"] for i in incidents}
    assert types == {"KeyError", "ZeroDivisionError"}


def test_same_fault_in_new_release_is_a_new_incident(control_plane):
    """A recurrence after a deploy is a new unit of work, not a reopened old
    one — the release is part of the cluster signature by design."""
    svc1 = make_service(control_plane, release="r1")
    svc1.get("/boom")
    svc2 = make_service(control_plane, release="r2")
    svc2.get("/boom")
    incidents = control_plane.get("/incidents").json()
    assert len(incidents) == 2


def test_healthy_requests_capture_nothing(control_plane):
    svc = make_service(control_plane)
    for _ in range(10):
        assert svc.get("/fine").status_code == 200
    assert control_plane.get("/incidents").json() == []


def test_identity_hook_reads_principal_before_redaction():
    """The identity hook sees the raw headers; the stored request does not."""
    events = []
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise KeyError("x")

    app.add_middleware(CaptureMiddleware, service="svc", deliver=events.append,
                       identity=lambda h: {"sub": h.get("authorization", "")[-3:]})
    TestClient(app, raise_server_exceptions=False).get(
        "/boom", headers={"Authorization": "Bearer secret-abc"})
    assert events[0]["identity"] == {"sub": "abc"}
    assert events[0]["request"]["headers"]["authorization"] == "[redacted]"


def test_a_handled_server_error_is_reported_and_reproduces(tmp_path):
    """An app that catches its own error and answers 500 is still failing:
    the capture reports it at the route's handler, and replay reproduces it."""
    events = []
    app = FastAPI()

    @app.get("/save")
    def save():
        from fastapi.responses import JSONResponse
        try:
            raise RuntimeError("disk full")
        except RuntimeError:
            return JSONResponse({"error": "could not save"}, status_code=500)

    app.add_middleware(CaptureMiddleware, service="svc", deliver=events.append,
                       handled_errors=True)
    TestClient(app, raise_server_exceptions=False).get("/save")
    assert events and events[0]["exception_type"] == "HTTP500" and events[0]["handled"]
    assert events[0]["frames"][0]["function"] == "save"

    # the replay engine treats the same handled 500 on every replay as reproduced
    (tmp_path / "svcmod.py").write_text(
        "from fastapi import FastAPI\n"
        "from fastapi.responses import JSONResponse\n"
        "from libs.kintsugi_capture import CaptureMiddleware\n"
        "app = FastAPI()\n"
        "@app.get('/save')\n"
        "def save():\n"
        "    return JSONResponse({'error': 'could not save'}, status_code=500)\n"
        "app.add_middleware(CaptureMiddleware, service='svc', handled_errors=True)\n")
    from control_plane.reproduction.engine import reproduce
    result = reproduce(events[0], "svcmod:app", workdir=str(tmp_path))
    assert result.reproduced, result.reason


def test_a_sqlite_snapshot_makes_a_data_dependent_failure_reproducible(tmp_path, monkeypatch):
    """A crash that only happens with a certain row present: without the
    snapshot the replay sees an empty database; with it, the crash recurs."""
    import sqlite3
    db = tmp_path / "live.db"
    con = sqlite3.connect(db)
    con.execute("create table notes (id integer primary key, body text)")
    con.execute("insert into notes values (1, NULL)")
    con.commit()
    con.close()
    (tmp_path / "notesvc.py").write_text(
        "import os, sqlite3\n"
        "from fastapi import FastAPI\n"
        "from libs.kintsugi_capture import CaptureMiddleware\n"
        "DB = os.environ.get('NOTES_DB', 'missing.db')\n"
        "app = FastAPI()\n"
        "@app.get('/notes/{nid}')\n"
        "def note(nid: int):\n"
        "    con = sqlite3.connect(DB)\n"
        "    con.execute('create table if not exists notes (id integer primary key, body text)')\n"
        "    row = con.execute('select body from notes where id = ?', (nid,)).fetchone()\n"
        "    return {'length': len(row[0]) if row else 0}\n"
        "app.add_middleware(CaptureMiddleware, service='notes', snapshot_sqlite=DB, snapshot_env='NOTES_DB')\n")
    monkeypatch.setenv("NOTES_DB", str(db))
    monkeypatch.syspath_prepend(str(tmp_path))
    import importlib
    import notesvc
    importlib.reload(notesvc)
    events = []
    notesvc.app.user_middleware.clear()
    notesvc.app.middleware_stack = None
    notesvc.app.add_middleware(CaptureMiddleware, service="notes", deliver=events.append,
                               snapshot_sqlite=str(db), snapshot_env="NOTES_DB")
    TestClient(notesvc.app, raise_server_exceptions=False).get("/notes/1")
    assert events and events[0]["exception_type"] == "TypeError" and events[0]["snapshot"]["data"]

    from control_plane.reproduction.engine import reproduce
    monkeypatch.delenv("NOTES_DB")
    with_snapshot = reproduce(events[0], "notesvc:app", workdir=str(tmp_path))
    without = reproduce({k: v for k, v in events[0].items() if k != "snapshot"}, "notesvc:app",
                        workdir=str(tmp_path))
    assert with_snapshot.reproduced, with_snapshot.reason
    assert not without.reproduced
