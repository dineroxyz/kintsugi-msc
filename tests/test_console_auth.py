"""Operator sign-in: what is protected, how sessions and forms are
secured, and that nothing redirects off-site."""

from __future__ import annotations

import importlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, password="correct horse"):
    study = tmp_path / "study"
    study.mkdir(exist_ok=True)
    monkeypatch.setenv("KINTSUGI_STUDY_DIR", str(study))
    monkeypatch.setenv("KINTSUGI_CERT_DIR", str(study / "certificates"))
    monkeypatch.setenv("KINTSUGI_BANK_PATH", str(study / "bank.jsonl"))
    monkeypatch.setenv("KINTSUGI_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("KINTSUGI_ADMIN_USER", "operator")
    if password is None:
        monkeypatch.delenv("KINTSUGI_ADMIN_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("KINTSUGI_ADMIN_PASSWORD", password)
    import console.auth as auth
    import console.app as app
    importlib.reload(auth)
    app = importlib.reload(app)
    auth._failures.clear()
    from control_plane.db import init_db
    init_db()                 # the lifespan hook only runs inside `with`
    return TestClient(app.app, base_url="https://testserver")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    return _client(monkeypatch, tmp_path)


def csrf(client, path="/login"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def sign_in(client, password="correct horse", nxt="/incidents"):
    return client.post("/login", data={"csrf": csrf(client), "next": nxt,
                                       "user": "operator", "password": password},
                       follow_redirects=False)


def test_operations_pages_need_sign_in(client):
    r = client.get("/incidents", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/incidents"
    assert client.get("/api/incidents").status_code == 401


def test_public_pages_stay_public(client):
    for path in ("/", "/results", "/certificates", "/practice", "/guide", "/privacy"):
        assert client.get(path).status_code == 200, path


def test_ingest_is_not_blocked_by_the_sign_in_gate(client):
    event = {"service": "alpha", "release": "r2", "exception_type": "KeyError",
             "exception_message": "'x'", "request": {"method": "GET", "path": "/x"},
             "external_calls": [],
             "frames": [{"file": "/srv/alpha/main.py", "line": 10,
                         "function": "handler", "code": "x['x']"}]}
    r = client.post("/api/ingest/event", json=event)
    assert r.status_code == 200 and r.json()["capture_count"] == 1


def test_correct_sign_in_sets_a_secure_session(client):
    r = sign_in(client)
    assert r.status_code == 303 and r.headers["location"] == "/incidents"
    cookie = r.headers["set-cookie"].lower()
    assert "kintsugi_session=" in cookie and "httponly" in cookie
    assert "samesite=lax" in cookie and "secure" in cookie
    assert client.get("/incidents").status_code == 200
    assert client.get("/api/incidents").status_code == 200
    assert "Sign out" in client.get("/results").text


def test_wrong_password_is_rejected_without_saying_which_field(client):
    r = sign_in(client, password="nope")
    assert r.status_code == 401
    assert "email and password do not match" in r.text
    assert "kintsugi_session" not in r.headers.get("set-cookie", "")


def test_repeated_failures_lock_the_address_out(client):
    for _ in range(5):
        assert sign_in(client, password="nope").status_code == 401
    r = sign_in(client)                       # even the right password
    assert r.status_code == 429 and "Too many attempts" in r.text


def test_forged_or_missing_form_token_is_refused(client):
    r = client.post("/login", data={"csrf": "forged", "user": "operator",
                                    "password": "correct horse"},
                    follow_redirects=False)
    assert r.status_code == 400 and "expired" in r.text


@pytest.mark.parametrize("nxt", ["//evil.example", "https://evil.example",
                                 "/\\\\evil.example", "javascript:alert(1)"])
def test_sign_in_never_redirects_off_site(client, nxt):
    r = sign_in(client, nxt=nxt)
    assert r.status_code == 303 and r.headers["location"] == "/results"


def test_forged_session_cookie_is_ignored(client):
    client.cookies.set("kintsugi_session", "eyJ1Ijoib3BlcmF0b3IifQ.forged.sig")
    assert client.get("/incidents", follow_redirects=False).status_code == 303


def test_sign_out_needs_the_form_token(client):
    sign_in(client)
    r = client.post("/logout", data={"csrf": "forged"}, follow_redirects=False)
    assert "kintsugi_session" not in r.headers.get("set-cookie", "")
    assert client.get("/incidents", follow_redirects=False).status_code == 200
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/results").text).group(1)
    r = client.post("/logout", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303 and "kintsugi_session" in r.headers["set-cookie"]
    assert client.get("/incidents", follow_redirects=False).status_code == 303


def test_operator_sign_in_is_impossible_without_a_password(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, password=None)
    r = client.post("/login", data={"csrf": csrf(client), "user": "operator",
                                    "password": "anything"}, follow_redirects=False)
    assert r.status_code == 401 and "kintsugi_session" not in r.headers.get("set-cookie", "")


def test_security_headers(client):
    h = client.get("/").headers
    assert h["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
