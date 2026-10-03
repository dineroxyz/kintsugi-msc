"""Console routes: every surface renders from real artefacts, the explorer
and detail fragments work, and nothing is a dead end."""

from __future__ import annotations

import importlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "study", "main")


def _client(monkeypatch, study_dir, bank=None):
    monkeypatch.setenv("KINTSUGI_STUDY_DIR", str(study_dir))
    monkeypatch.setenv("KINTSUGI_CERT_DIR", os.path.join(str(study_dir), "certificates"))
    monkeypatch.setenv("KINTSUGI_BANK_PATH", str(bank or os.path.join(str(study_dir), "bank.jsonl")))
    import console.app as module
    module = importlib.reload(module)
    return TestClient(module.app)


@pytest.fixture()
def empty(tmp_path, monkeypatch):
    study = tmp_path / "study"
    study.mkdir()
    return _client(monkeypatch, study), study


@pytest.fixture()
def real(monkeypatch):
    if not os.path.exists(os.path.join(MAIN, "trials.jsonl")):
        pytest.skip("main study log not present")
    return _client(monkeypatch, MAIN,
                   os.path.join(ROOT, "study", "supp", "practice_bank.jsonl"))


def test_empty_surfaces_render(empty):
    client, _ = empty
    for path in ("/", "/results", "/certificates", "/practice", "/guide", "/privacy", "/login"):
        r = client.get(path)
        assert r.status_code == 200
        assert "Kintsugi" in r.text and "Abe Oluwaseyi Elizabeth" in r.text


def test_small_log_renders_with_integrity_warning(empty):
    client, study = empty
    rows = [
        {"entry": "F01", "arm": "C3", "outcome": "recovered", "mttr_seconds": 11.0,
         "timings": {}, "detail": {}, "expected_c3": "healed", "seed": 1},
        {"entry": "F11", "arm": "C3", "outcome": "refused", "mttr_seconds": None,
         "timings": {}, "detail": {"heal_reason": "reproduction refused: x"},
         "expected_c3": "refused", "seed": 1},
    ]
    with open(study / "trials.jsonl", "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)
    text = client.get("/results").text
    assert "F01" in text and "Left alone" in text and "11.0 s" in text
    assert "do not add up" in text          # 2 bugs, not 60: flagged, not hidden


def test_api_is_mounted(empty):
    client, _ = empty
    assert client.get("/api/health").json()["component"] == "control-plane"


def test_static_assets_are_served(empty):
    client, _ = empty
    assert client.get("/static/console.css").status_code == 200
    assert client.get("/static/console.js").status_code == 200


def test_results_page_on_real_data(real):
    text = real.get("/results").text
    assert "Data check passed: 720 test records" in text
    assert len(re.findall(r'class="mx-cell"', text)) == 240      # 60 bugs x 4
    assert text.count('role="row"') == 4
    for label in ("Fixed", "Left alone", "Gave up", "Still broken"):
        assert label in text
    assert "Showing 1 to 25 of 720 tests" in text
    assert "none fixed" in text               # C0 has no time, shown as such


def test_explorer_fragment_filters_sorts_and_paginates(real):
    text = real.get("/fragment/explorer?approach=C3&outcome=refused").text
    assert "of 45 tests" in text
    text = real.get("/fragment/explorer?bug=F03&sort=time&order=desc").text
    assert "of 12 tests" in text and 'aria-sort="descending"' in text
    text = real.get("/fragment/explorer?page=29").text
    assert "Page 29 of 29" in text and "Showing 701 to 720" in text
    text = real.get("/fragment/explorer?bug=nothing").text
    assert "No tests match" in text and "data-ex-reset" in text


def test_deep_link_renders_the_filtered_explorer(real):
    text = real.get("/results?approach=C1&run=2").text
    assert "Showing 1 to 25 of 60 tests" in text
    assert '<option value="C1" selected>' in text


def test_bug_and_test_details(real):
    assert "Bug F03" in real.get("/fragment/bug/F03").text
    assert real.get("/bugs/F03").status_code == 200
    assert real.get("/bugs/NOPE").status_code == 404
    text = real.get("/fragment/test/0").text
    assert "Test 0" in text and "<dt>Result</dt>" in text and "Run" in text
    assert real.get("/tests/0").status_code == 200
    assert real.get("/tests/999999").status_code == 404


def test_certified_test_links_to_its_certificate(real):
    trials = [json.loads(l) for l in open(os.path.join(MAIN, "trials.jsonl"))]
    i = next(i for i, t in enumerate(trials) if t["detail"].get("certificates"))
    assert "/certificates/" in real.get(f"/fragment/test/{i}").text


def test_every_internal_link_resolves(real):
    links = set()
    for page in ("/", "/results", "/guide", "/privacy", "/certificates/0"):
        links |= set(re.findall(r'href="(/[^"#]*)', real.get(page).text))
    for link in sorted(links):
        if link.startswith("/static/"):
            continue
        assert real.get(link.replace("&amp;", "&")).status_code == 200, link


def test_data_endpoints(real):
    data = real.get("/data/trials.json").json()
    assert len(data) == 1260
    assert real.get("/data/verification.json").status_code == 200


def test_home_page_counts_come_from_the_records(real):
    text = real.get("/").text
    assert "Software that repairs itself" in text
    assert "15 of 15" in text and "in every run" in text   # per bug, not per test
    assert "across all 1,260 tests" in text
    assert "92%" in text and "43%" in text          # supplementary study
    assert "fonts.googleapis" not in text           # fonts are self-hosted


def test_guide_and_privacy_pages(real):
    guide = real.get("/guide").text
    for anchor in ("grid", "tests", "verify", "glossary", "keys"):
        assert f'id="{anchor}"' in guide
    privacy = real.get("/privacy").text
    assert "kintsugi_session" in privacy and "No cookies for visitors" in privacy
