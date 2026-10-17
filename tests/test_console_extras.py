"""Public-key signatures, the trust endpoints, connected services, the
status and built pages, and the pull-request writer."""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "study", "main")
SEED = base64.b64encode(bytes(range(32))).decode()


# ------------------------------------------------------------ signatures

def test_ed25519_sign_and_attest(monkeypatch):
    monkeypatch.setenv("KINTSUGI_ED25519_SEED", SEED)
    from control_plane.certificates import pubsig
    block = pubsig.sign_hash("ab" * 32)
    assert pubsig.verify(block["key"], ("ab" * 32).encode(), block["sig"])
    assert not pubsig.verify(block["key"], ("cd" * 32).encode(), block["sig"])
    att = pubsig.attest("t", ["a" * 64, "b" * 64])
    assert pubsig.verify_attestation(att)
    att["hashes"].append("c" * 64)
    assert not pubsig.verify_attestation(att)


def test_new_certificates_carry_a_public_signature(monkeypatch, tmp_path):
    monkeypatch.setenv("KINTSUGI_ED25519_SEED", SEED)
    from control_plane.certificates.issuer import (CertificateError, CertificateStore,
                                                   canonical_bytes)
    from control_plane.loop import Outcome
    store = CertificateStore(str(tmp_path))
    out = Outcome("deployed", "admitted", signature="KeyError@x:1",
                  reproduction={"reason": "failed identically on 5/5 replays"},
                  candidates=[{"attempt": 1, "diff": "--- a/x\n+++ b/x\n", "conditions": {}}],
                  verification_record=json.dumps({"payload": {}, "hmac": "x"}))
    cert = store.issue({"exception_type": "KeyError"}, out, "alpha.main:app")
    assert cert["ed25519"]["alg"] == "Ed25519"
    assert hashlib.sha256(canonical_bytes(cert)).hexdigest() == cert["hash"]
    store.verify_chain()
    path = tmp_path / "000000.json"
    tampered = json.loads(path.read_text())
    tampered["ed25519"]["sig"] = base64.b64encode(b"\0" * 64).decode()
    path.write_text(json.dumps(tampered))
    with pytest.raises(CertificateError, match="public signature"):
        store.verify_chain()


@pytest.mark.skipif(not os.path.exists(os.path.join(MAIN, "attestation.json")),
                    reason="no main-study attestation")
def test_main_study_attestation_covers_every_certificate():
    from control_plane.certificates import pubsig
    from control_plane.certificates.issuer import CertificateStore
    att = json.load(open(os.path.join(MAIN, "attestation.json")))
    assert pubsig.verify_attestation(att)
    assert att["hashes"] == [c["hash"] for c in CertificateStore(
        os.path.join(MAIN, "certificates")).all()]


# --------------------------------------------------------------- the site

@pytest.fixture()
def site(monkeypatch, tmp_path):
    if not os.path.exists(os.path.join(MAIN, "trials.jsonl")):
        pytest.skip("main study not present")
    monkeypatch.setenv("KINTSUGI_STUDY_DIR", MAIN)
    monkeypatch.setenv("KINTSUGI_SESSION_SECRET", "t")
    monkeypatch.delenv("KINTSUGI_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("KINTSUGI_LIVE", raising=False)
    monkeypatch.delenv("KINTSUGI_ED25519_SEED", raising=False)
    monkeypatch.delenv("KINTSUGI_STATUS_TARGETS", raising=False)
    import console.auth as auth
    import console.accounts as A
    import console.app as app
    importlib.reload(auth)
    app = importlib.reload(app)
    from control_plane.db import get_session, init_db
    init_db()
    with get_session() as s:
        for t in (A.Service, A.Run, A.User):
            s.execute(t.__table__.delete())
        s.commit()
    A._signups.clear()
    A._ingest_times.clear()
    return TestClient(app.app, base_url="https://testserver"), A


def test_canonical_bytes_hash_to_the_certificate_id(site):
    c, _ = site
    meta = c.get("/certificates/5.json").json()
    raw = c.get("/certificates/5/canonical").content
    assert hashlib.sha256(raw).hexdigest() == meta["hash"]
    assert json.loads(raw)["prev_hash"] == hashlib.sha256(
        c.get("/certificates/4/canonical").content).hexdigest()


def test_trust_pages_and_published_key(site):
    c, _ = site
    key = c.get("/.well-known/kintsugi-key.json").json()
    att = c.get("/data/attestation.json").json()
    assert key["key"] == att["key"] and key["key_id"]
    text = c.get("/trust").text
    assert "Why you can trust the record" in text and key["key_id"] in text
    assert 'type="module" src="/static/verify.js"' in c.get("/certificates/0").text


def test_built_page_counts_real_code(site):
    c, _ = site
    text = c.get("/built").text
    nums = [int(n.replace(",", "")) for n in re.findall(r"<dd>([\d,]+)</dd>", text)]
    assert nums[0] > 5000 and nums[2] > 100       # lines of code, test functions
    assert "The repair loop" in text and "Verification gate" in text


def test_status_page_without_targets(site):
    c, _ = site
    assert "No services are being watched" in c.get("/status").text


def _signup(c):
    tok = re.search(r'name="csrf" value="([^"]+)"', c.get("/signup").text).group(1)
    c.post("/signup", data={"csrf": tok, "email": "dev@example.com",
                            "password": "a long passphrase here", "agree": "yes"})


def _service_token(c):
    text = c.get("/account").text
    return re.search(r'name="csrf" value="([^"]+)"', text[text.index('action="/account/services"'):]).group(1)


EVENT = {"exception_type": "ValueError", "exception_message": "bad",
         "frames": [{"file": "/srv/app/main.py", "line": 3, "function": "f", "code": "x"}],
         "request": {"method": "GET", "path": "/x"}, "release": "v1"}


def test_connect_a_service_and_receive_its_failures(site):
    c, A = site
    _signup(c)
    r = c.post("/account/services", data={"csrf": _service_token(c), "name": "detector-api"})
    key = re.search(r"(ks_[A-Za-z0-9_-]+)", r.text).group(1)
    assert f"/connect/{key}" in r.text                      # shown once, with setup steps
    assert key not in c.get("/account").text                # and never again
    assert c.post(f"/connect/{key}/ingest/event", json=EVENT).status_code == 200
    assert c.post(f"/connect/{key}/ingest/event", json=EVENT).json()["capture_count"] == 2
    page = c.get("/account").text
    assert "detector-api" in page and "ValueError" in page


def test_connect_rejects_bad_keys_and_bad_events(site):
    c, A = site
    _signup(c)
    r = c.post("/account/services", data={"csrf": _service_token(c), "name": "api"})
    key = re.search(r"(ks_[A-Za-z0-9_-]+)", r.text).group(1)
    assert c.post("/connect/ks_nope/ingest/event", json=EVENT).status_code == 401
    assert c.post(f"/connect/{key}/ingest/event", json={"hello": 1}).status_code == 400
    assert c.post(f"/connect/{key}/ingest/event", content=b"not json").status_code == 400


def test_connect_rate_limit(site, monkeypatch):
    c, A = site
    monkeypatch.setattr(A, "INGEST_PER_MINUTE", 2)
    _signup(c)
    r = c.post("/account/services", data={"csrf": _service_token(c), "name": "api"})
    key = re.search(r"(ks_[A-Za-z0-9_-]+)", r.text).group(1)
    codes = [c.post(f"/connect/{key}/ingest/event", json=EVENT).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_service_names_are_validated(site):
    c, _ = site
    _signup(c)
    r = c.post("/account/services", data={"csrf": _service_token(c), "name": "Bad Name!"})
    assert r.status_code == 400 and "lower-case" in r.text


# ------------------------------------------------------------- pull requests

def test_pull_request_writer_makes_branches_commits_and_a_pr(monkeypatch):
    from control_plane.integrations import github_pr
    monkeypatch.setenv("KINTSUGI_GITHUB_TOKEN", "t")
    monkeypatch.setenv("KINTSUGI_GITHUB_REPO", "o/shop")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET" and "/git/ref/heads/" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "abc"}})
        if request.method == "GET" and "/contents/" in request.url.path:
            return httpx.Response(404)
        if request.url.path.endswith("/pulls"):
            body = json.loads(request.content)
            assert body["base"].startswith("incident/") and body["head"].startswith("kintsugi/fix-")
            assert "Nobody wrote this patch by hand" in body["body"]
            return httpx.Response(201, json={"html_url": "https://github.com/o/shop/pull/1", "number": 1})
        return httpx.Response(201, json={})

    monkeypatch.setattr(github_pr, "_client", lambda: httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)))
    pr = github_pr.open_repair_pr(
        run="r1", entry_id="F03", title="Missing guard", buggy={"alpha/main.py": "bug"},
        fixed={"alpha/main.py": "fix"}, regression_test="def test_x(): pass",
        evidence={"conditions": {"applies": True}, "certificate_hash": "f" * 64})
    assert pr == {"url": "https://github.com/o/shop/pull/1", "number": 1}
    assert ("POST", "/repos/o/shop/git/refs") in calls
    assert sum(1 for m, p in calls if m == "PUT") == 3     # bug, fix, regression test


def test_pull_requests_are_off_without_configuration(monkeypatch):
    from control_plane.integrations import github_pr
    monkeypatch.delenv("KINTSUGI_GITHUB_TOKEN", raising=False)
    assert not github_pr.configured()


def test_live_chain_pages(site, monkeypatch, tmp_path):
    c, _ = site
    import console.live as L
    monkeypatch.setattr(L, "LIVE_CERTS", str(tmp_path / "live-certs"))
    assert "No certificates in this chain yet" in c.get("/live-certificates").text
    assert 'aria-current=page>Study repairs' in c.get("/certificates").text


def test_change_password(site):
    c, A = site
    _signup(c)
    text = c.get("/account").text
    tok = re.search(r'name="csrf" value="([^"]+)"', text[text.index('action="/account/password"'):]).group(1)
    bad = c.post("/account/password", data={"csrf": tok, "current": "wrong", "new": "another long one"})
    assert bad.status_code == 400 and "current password is not right" in bad.text
    ok = c.post("/account/password", data={"csrf": tok, "current": "a long passphrase here",
                                           "new": "another long one"})
    assert "Password changed" in ok.text
    assert A.authenticate("dev@example.com", "another long one") is not None
    assert A.authenticate("dev@example.com", "a long passphrase here") is None


def test_explanations_are_plain_text():
    from control_plane.study.live import plain_text
    assert plain_text("# What went wrong\n\n**A** bug.\n\n- It was fixed.") == "A bug. It was fixed."


def test_verification_report_is_readable(site):
    c, _ = site
    text = c.get("/verification").text
    assert "Certificate check report" in text and "494 of 494" in text
    assert "Caught: its contents no longer matched its fingerprint" in text
    assert 'href="/data/verification.json" download' in text
    assert c.get("/data/verification.json").json()["replay"]["verified"] == 130



def test_correctness_section_is_honest(site):
    c, _ = site
    text = c.get("/results").text
    assert "Did the fixes change anything else?" in text and "Wrong elsewhere" in text
    assert "/results#correct-title" in c.get("/").text
    data = c.get("/data/correctness.json").json()
    assert data["totals"]["main"] == 127


def test_owner_accounts_are_unlimited(site, monkeypatch):
    c, A = site
    import console.app as app
    monkeypatch.setenv("KINTSUGI_LIVE", "1")
    monkeypatch.setattr(app.L, "enabled", lambda: True)
    monkeypatch.setattr(A, "RUNS_PER_ACCOUNT_PER_DAY", 1)
    _signup(c)
    user = A.authenticate("dev@example.com", "a long passphrase here")
    for _ in range(3):
        A.start_run(user.id, "F03")
    assert "Owner account" not in c.get("/account").text
    monkeypatch.setenv("KINTSUGI_UNLIMITED_EMAILS", "someone@else.com, DEV@example.com")
    assert "Owner account: unlimited real runs." in c.get("/account").text
    assert "Owner account: unlimited real runs." in c.get("/live").text
    assert A.runs_left(user.id)["site"] == A.RUNS_PER_DAY      # owner runs leave visitors' cap alone
