"""Self-practice: rehearsals bank evidence without deploying anything, the
scheduler spreads work across the corpus and respects the idle signal, and
banked rehearsals surface as memory notes for resembling real incidents.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest

from control_plane.deploy.live import ServiceProcess, free_port
from control_plane.practice.mode import (PracticeBank, PracticeScheduler,
                                         practice_key, rehearse)
from corpus import load_corpus, load_entry
from corpus.injector import fix_diff, inject

PLATFORM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES = os.path.join(PLATFORM_ROOT, "services")


@pytest.fixture(scope="module")
def clean_beta():
    proc = ServiceProcess(SERVICES, "beta.main:app", free_port(), "b1",
                          PLATFORM_ROOT)
    proc.start()
    os.environ["BETA_URL"] = f"http://127.0.0.1:{proc.port}"
    yield proc
    proc.stop()


def scripted(entry, tmp_path, tag):
    faulty = inject(entry, SERVICES, str(tmp_path / f"diff-{tag}"))
    diff = fix_diff(entry, faulty)
    return lambda _evidence: diff


def test_rehearsal_banks_a_would_heal_without_deploying(clean_beta, tmp_path):
    entry = load_entry("F01")
    rehearsal = rehearse(entry, str(tmp_path / "w"),
                         complete=scripted(entry, tmp_path, "f01"))
    assert rehearsal.status == "would_heal", rehearsal.reason
    assert rehearsal.patch and "DISCOUNTS" in rehearsal.patch
    assert rehearsal.regression_test
    assert rehearsal.signature
    # the clean tree was never touched: the fault still injects cleanly,
    # which the injector would refuse if the source had changed
    inject(entry, SERVICES, str(tmp_path / "still-clean"))


def test_rehearsal_banks_refusals_with_their_reason(clean_beta, tmp_path):
    entry = load_entry("F11")     # the stock race

    def must_not_be_called(_evidence):
        raise AssertionError("model consulted for a refusal rehearsal")

    rehearsal = rehearse(entry, str(tmp_path / "w"),
                         complete=must_not_be_called)
    assert rehearsal.status == "refused", (rehearsal.status, rehearsal.reason)
    assert rehearsal.reason


def test_scheduler_spreads_rehearsals_and_respects_idle(clean_beta, tmp_path):
    corpus = [e for e in load_corpus() if e.id in ("F01", "F12", "F14")]
    bank = PracticeBank(str(tmp_path / "bank.jsonl"))

    completions = {e.id: scripted(e, tmp_path, e.id) for e in corpus}
    scheduler = PracticeScheduler(
        corpus, bank, str(tmp_path / "work"),
        complete=lambda ev: completions[_entry_of(ev, corpus)](ev))

    # a busy platform must not practise
    scheduler.idle = lambda: False
    assert scheduler.run(3) == []
    assert bank.all() == []

    scheduler.idle = lambda: True
    done = scheduler.run(6)
    assert len(done) == 6
    counts = bank.counts()
    assert counts == {"F01": 2, "F12": 2, "F14": 2}, counts
    assert all(r["status"] == "would_heal" for r in bank.all())


def _entry_of(evidence_text: str, corpus):
    """The scripted completion needs to know which entry is rehearsing;
    the evidence names the provoking request, which is unique here."""
    markers = {"F01": "/quote", "F12": "/cancel", "F14": "/reserve"}
    for entry_id, marker in markers.items():
        if marker in evidence_text.split("PROVOKING REQUEST")[1][:120]:
            return entry_id
    raise AssertionError("could not attribute evidence to an entry")


def test_cross_service_fault_rehearses_with_faulty_dependency(clean_beta,
                                                              tmp_path):
    """F21's fault lives in beta but crashes alpha; practice must run the
    faulty beta as a subprocess so the rehearsal reaches the real failure,
    and the admitted patch is the alpha-side defensive coercion."""
    import difflib
    entry = load_entry("F21")
    before = open(os.path.join(SERVICES, "alpha/main.py")).read()
    after = before.replace('    unit = resp.json()["price"]\n',
                           '    unit = float(resp.json()["price"])\n')
    diff = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="a/alpha/main.py", tofile="b/alpha/main.py"))

    rehearsal = rehearse(entry, str(tmp_path / "w"), complete=lambda _e: diff)
    assert rehearsal.status == "would_heal", (rehearsal.status,
                                              rehearsal.reason)
    assert rehearsal.capture["service"] == "alpha"
    assert "float" in rehearsal.patch


def test_banked_rehearsal_recalls_for_resembling_incident(clean_beta, tmp_path):
    entry = load_entry("F01")
    bank = PracticeBank(str(tmp_path / "bank.jsonl"))
    rehearsal = rehearse(entry, str(tmp_path / "w"),
                         complete=scripted(entry, tmp_path, "f01"))
    bank.bank(rehearsal)

    real_incident = {
        "service": "alpha", "exception_type": "TypeError",
        "request": {"path": "/quote"},
    }
    notes = bank.notes_for(real_incident)
    assert len(notes) == 1
    assert "rehearsed in self-practice" in notes[0]
    assert "DISCOUNTS" in notes[0]

    unrelated = {"service": "alpha", "exception_type": "KeyError",
                 "request": {"path": "/orders/7"}}
    assert bank.notes_for(unrelated) == []

    # numeric path segments generalise: /orders/7/x resembles /orders/9/x
    assert practice_key("alpha", "KeyError", "/orders/7/discounted") == \
        practice_key("alpha", "KeyError", "/orders/9/discounted")
