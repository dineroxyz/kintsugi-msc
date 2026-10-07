"""Visitor accounts: sign-up, sign-in, roles, daily allowances, deletion."""

from __future__ import annotations

import importlib
import os
import re
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient

PW = "a long enough passphrase"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("KINTSUGI_STUDY_DIR", str(tmp_path))
    monkeypatch.setenv("KINTSUGI_SESSION_SECRET", "t")
    monkeypatch.setenv("KINTSUGI_ADMIN_PASSWORD", "operator pass")
    monkeypatch.setenv("KINTSUGI_LIVE", "1")
    monkeypatch.setenv("KINTSUGI_LIVE_RECORDINGS", str(tmp_path / "rec"))
    import console.auth as auth
    import console.accounts as A
    import console.live as L
    import console.app as app
    importlib.reload(auth)
    app = importlib.reload(app)
    from control_plane.db import get_session, init_db
    init_db()
    with get_session() as s:                       # a clean slate per test
        s.execute(A.Run.__table__.delete())
        s.execute(A.User.__table__.delete())
        s.commit()
    A._signups.clear()
    auth._failures.clear()
    started = []

    def fake_start(entry_id, *, complete=None, on_finish=None):
        started.append(entry_id)
        if on_finish:
            on_finish(outcome="recovered", mttr=12.0, recording="20261007-000000-F03",
                      certificate=1)
        return types.SimpleNamespace(id=f"run{len(started)}")
    monkeypatch.setattr(L, "start", fake_start)
    monkeypatch.setattr(L, "busy", lambda: False)
    client = TestClient(app.app, base_url="https://testserver")
    return client, A, started


def token(c, path):
    """The anti-forgery token of the page's own form (not the nav's sign-out)."""
    text = c.get(path).text
    m = re.search(r'data-csrf="([^"]+)"', text)
    if m:
        return m.group(1)
    action = {"/signup": "/signup", "/login": "/login", "/account": "/account/delete"}[path]
    form = text[text.index(f'action="{action}"'):]
    return re.search(r'name="csrf" value="([^"]+)"', form).group(1)


def signup(c, email="ada@example.com", password=PW, agree="yes"):
    return c.post("/signup", data={"csrf": token(c, "/signup"), "email": email,
                                   "password": password, "agree": agree},
                  follow_redirects=False)


def test_passwords_are_salted_hashes_never_plain(env):
    _, A, _ = env
    h1, h2 = A.hash_password(PW), A.hash_password(PW)
    assert h1 != h2 and PW not in h1 and h1.startswith("scrypt$")
    assert A.verify_password(PW, h1) and not A.verify_password("wrong", h1)


def test_sign_up_creates_an_account_and_signs_in(env):
    c, A, _ = env
    r = signup(c)
    assert r.status_code == 303 and r.headers["location"] == "/live?welcome=1"
    assert "kintsugi_session" in r.headers["set-cookie"]
    user = A.get_user("ADA@example.com ")                 # emails are normalised
    assert user and user.password_hash != PW
    assert "Your account is ready" in c.get("/live?welcome=1").text
    assert "Account" in c.get("/results").text


@pytest.mark.parametrize("email,password,agree,msg", [
    ("not-an-email", PW, "yes", "valid email"),
    ("ada@example.com", "short", "yes", "at least 10"),
    ("ada@example.com", "ada@example.com-pass", "yes", "does not contain"),
    ("ada@example.com", PW, "", "privacy policy"),
])
def test_sign_up_validates(env, email, password, agree, msg):
    c, A, _ = env
    r = signup(c, email, password, agree)
    assert r.status_code == 400 and msg in r.text
    assert A.get_user("ada@example.com") is None


def test_duplicate_email_is_refused(env):
    c, _, _ = env
    signup(c)
    c.cookies.clear()
    r = signup(c)
    assert r.status_code == 400 and "already exists" in r.text


def test_sign_ups_from_one_address_are_limited(env):
    c, _, _ = env
    for i in range(3):
        assert signup(c, f"u{i}@example.com").status_code == 303
        c.cookies.clear()
    assert signup(c, "u9@example.com").status_code == 429


def test_account_holder_signs_in_with_email(env):
    c, _, _ = env
    signup(c)
    c.cookies.clear()
    r = c.post("/login", data={"csrf": token(c, "/login"), "user": "ada@example.com",
                               "password": PW, "next": "/account"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/account"
    c.cookies.clear()
    bad = c.post("/login", data={"csrf": token(c, "/login"), "user": "ada@example.com",
                                 "password": "nope nope nope"}, follow_redirects=False)
    assert bad.status_code == 401


def test_accounts_cannot_see_operator_data(env):
    c, _, _ = env
    signup(c)
    assert c.get("/incidents", follow_redirects=False).status_code == 403
    assert c.get("/api/incidents").status_code == 401
    assert "Errors" not in c.get("/results").text


def test_daily_allowance_is_enforced_and_runs_are_recorded(env):
    c, A, started = env
    signup(c)
    for i in range(A.RUNS_PER_ACCOUNT_PER_DAY):
        r = c.post("/live/run", data={"csrf": token(c, "/live"), "entry": "F03"})
        assert r.status_code == 200, r.text
    r = c.post("/live/run", data={"csrf": token(c, "/live"), "entry": "F03"})
    assert r.status_code == 403 and "used today's runs" in r.json()["error"]
    page = c.get("/account").text
    assert page.count("/live?replay=20261007-000000-F03") == A.RUNS_PER_ACCOUNT_PER_DAY
    assert "0</b> of 3 runs left" in page


def test_signed_out_visitors_cannot_start_runs(env):
    c, _, started = env
    r = c.post("/live/run", data={"csrf": token(c, "/live"), "entry": "F03"})
    assert r.status_code == 403 and "free account" in r.json()["error"]
    assert started == []


def test_unknown_bug_is_refused(env):
    c, _, started = env
    signup(c)
    r = c.post("/live/run", data={"csrf": token(c, "/live"), "entry": "../../etc"})
    assert r.status_code == 400 and started == []


def test_site_wide_ceiling(env, monkeypatch):
    c, A, _ = env
    monkeypatch.setattr(A, "RUNS_PER_DAY", 1)
    signup(c)
    assert c.post("/live/run", data={"csrf": token(c, "/live"), "entry": "F03"}).status_code == 200
    c.cookies.clear()
    signup(c, "bo@example.com")
    r = c.post("/live/run", data={"csrf": token(c, "/live"), "entry": "F03"})
    assert r.status_code == 403 and "whole site" in r.json()["error"]


def test_deleting_an_account_removes_everything(env):
    c, A, _ = env
    signup(c)
    c.post("/live/run", data={"csrf": token(c, "/live"), "entry": "F03"})
    t = token(c, "/account")
    r = c.post("/account/delete", data={"csrf": t, "confirm": "wrong@example.com"},
               follow_redirects=False)
    assert A.get_user("ada@example.com") is not None             # needs confirmation
    r = c.post("/account/delete", data={"csrf": t, "confirm": "ada@example.com"},
               follow_redirects=False)
    assert r.status_code == 303 and A.get_user("ada@example.com") is None
    from control_plane.db import get_session
    from sqlalchemy import func, select
    with get_session() as s:                 # runs stay, but belong to no one
        assert s.scalar(select(func.count()).select_from(A.Run)) == 1
        assert s.scalar(select(func.count()).select_from(A.Run)
                        .where(A.Run.user_id.is_not(None))) == 0
    c.cookies.clear()
    assert c.get("/account", follow_redirects=False).status_code == 303
