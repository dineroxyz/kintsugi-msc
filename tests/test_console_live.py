"""The Live view: replay-only mode, the live-run guard, the event streams,
and a full live heal driven by a scripted model."""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def recording(dirpath, name, entry="F03", outcome="recovered"):
    events = [{"t": 0.0, "kind": "start", "entry": entry, "title": "x"},
              {"t": 0.01, "kind": "fault_deployed"},
              {"t": 0.02, "kind": "recovered", "mttr": 1.0},
              {"t": 0.03, "kind": "end", "outcome": outcome, "mttr": 1.0}]
    with open(os.path.join(dirpath, name + ".jsonl"), "w") as fh:
        fh.write(json.dumps({"kind": "meta", "entry": entry, "recorded": "7 October 2026",
                             "outcome": outcome, "mttr": 1.0, "model": "test"}) + "\n")
        for e in events:
            fh.write(json.dumps(e) + "\n")


def client(monkeypatch, tmp_path, *, live=False, password=None):
    rec = tmp_path / "rec"
    rec.mkdir(exist_ok=True)
    recording(str(rec), "20261007-000000-F03")
    monkeypatch.setenv("KINTSUGI_LIVE_RECORDINGS", str(rec))
    monkeypatch.setenv("KINTSUGI_LIVE_CERTS", str(tmp_path / "certs"))
    monkeypatch.setenv("KINTSUGI_STUDY_DIR", str(tmp_path))
    monkeypatch.setenv("KINTSUGI_SESSION_SECRET", "t")
    if live:
        monkeypatch.setenv("KINTSUGI_LIVE", "1")
    else:
        monkeypatch.delenv("KINTSUGI_LIVE", raising=False)
    if password:
        monkeypatch.setenv("KINTSUGI_ADMIN_PASSWORD", password)
    else:
        monkeypatch.delenv("KINTSUGI_ADMIN_PASSWORD", raising=False)
    import console.auth as auth
    import console.live as live_mod
    import console.app as app
    importlib.reload(auth)
    importlib.reload(live_mod)
    app = importlib.reload(app)
    return TestClient(app.app)


def token(c):
    return re.search(r'data-csrf="([^"]+)"', c.get("/live").text).group(1)


def test_replay_only_mode_offers_only_recorded_bugs(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    text = c.get("/live").text
    assert 'data-live="0"' in text and "recording of a real live run" in text
    options = re.findall(r'<option value="([A-Z0-9]+)"', text)
    assert options == ["F03"]
    assert "Replay a recorded run" not in text      # Break it already replays


def test_live_runs_are_refused_where_switched_off(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    r = c.post("/live/run", data={"csrf": token(c), "entry": "F03"})
    assert r.status_code == 403


def test_live_run_needs_a_valid_form_token(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, live=True)
    assert c.post("/live/run", data={"csrf": "forged", "entry": "F03"}).status_code == 400


def test_live_run_needs_sign_in_when_a_password_is_set(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, live=True, password="pw")
    text = c.get("/live").text
    assert 'data-live="0"' in text and "sign in" in text
    assert c.post("/live/run", data={"csrf": token(c), "entry": "F03"}).status_code == 403


def test_replay_stream_is_labelled_and_complete(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    body = c.get("/live/replay/20261007-000000-F03").text
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert events[0]["kind"] == "replay" and events[0]["recorded"] == "7 October 2026"
    assert [e["kind"] for e in events[1:]] == ["start", "fault_deployed", "recovered", "end"]


@pytest.mark.parametrize("name", ["nope", "..%2F..%2Fetc", "a/b"])
def test_unknown_or_unsafe_recordings_are_refused(monkeypatch, tmp_path, name):
    c = client(monkeypatch, tmp_path)
    assert c.get(f"/live/replay/{name}").status_code == 404


def test_full_live_heal_with_a_scripted_model():
    from control_plane.certificates.issuer import CertificateStore
    from control_plane.study.live import run_live
    from corpus import load_entry
    from corpus.injector import fix_diff, inject

    entry = load_entry("F03")
    diff = fix_diff(entry, inject(entry, os.path.join(ROOT, "services"),
                                  tempfile.mkdtemp() + "/t"))
    events = []
    result = run_live(entry, events.append, complete=lambda _: diff,
                      certificates=CertificateStore(tempfile.mkdtemp()))
    kinds = [e["kind"] for e in events if e["kind"] not in ("traffic", "token")]
    assert result["outcome"] == "recovered" and result["mttr_seconds"] > 0
    assert kinds == ["start", "traffic_started", "fault_deployed", "captured",
                     "reproduce_start", "reproduce_done", "candidate_start",
                     "gate_result", "deploying", "deployed", "certificate",
                     "probe", "recovered", "end"]
    traffic = [s for e in events if e["kind"] == "traffic" for s in e["samples"]]
    assert any(s[1] >= 500 for s in traffic) and any(s[1] < 500 for s in traffic)
    assert all(e["t"] >= 0 for e in events)
