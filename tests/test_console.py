"""Console smoke: every surface renders from real artefacts."""

from __future__ import annotations

import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def console(tmp_path, monkeypatch):
    study = tmp_path / "study"
    study.mkdir()
    monkeypatch.setenv("KINTSUGI_STUDY_DIR", str(study))
    monkeypatch.setenv("KINTSUGI_CERT_DIR", str(study / "certificates"))
    monkeypatch.setenv("KINTSUGI_BANK_PATH", str(study / "bank.jsonl"))
    import console.app as module
    module = importlib.reload(module)
    return TestClient(module.app), study


def test_empty_surfaces_render(console):
    client, _ = console
    for path in ("/", "/certificates", "/practice"):
        response = client.get(path)
        assert response.status_code == 200
        assert "KINTSUGI" in response.text


def test_overview_renders_trials(console):
    client, study = console
    rows = [
        {"entry": "F01", "arm": "C3", "outcome": "recovered",
         "mttr_seconds": 11.0, "timings": {}, "detail": {},
         "expected_c3": "healed"},
        {"entry": "F11", "arm": "C3", "outcome": "refused",
         "mttr_seconds": None, "timings": {},
         "detail": {"heal_reason": "reproduction refused: non-deterministic"},
         "expected_c3": "refused"},
    ]
    with open(study / "trials.jsonl", "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)
    text = client.get("/").text
    assert "F01" in text and "refused" in text and "11.0" in text


def test_api_is_mounted(console):
    client, _ = console
    assert client.get("/api/health").json()["component"] == "control-plane"
