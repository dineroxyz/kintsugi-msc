"""Hardening metrics: the banked suite's mutation score grows as the
platform practises, and recurrence tracking tells healed shapes that
stayed fixed from shapes that came back.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest

from control_plane.deploy.live import ServiceProcess, free_port
from control_plane.metrics.hardening import RecurrenceTracker, mutation_score
from control_plane.practice.mode import PracticeBank, rehearse
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


def test_mutation_score_grows_with_practice(clean_beta, tmp_path):
    pool = [e for e in load_corpus() if e.id in ("F01", "F12", "F14")]
    bank = PracticeBank(str(tmp_path / "bank.jsonl"))

    entry = load_entry("F01")
    bank.bank(rehearse(entry, str(tmp_path / "w1"),
                       complete=scripted(entry, tmp_path, "f01")))

    first = mutation_score(bank, pool, str(tmp_path / "m1"))
    assert first["suite_valid"], "banked suite must pass on the clean tree"
    assert first["per_mutant"]["F01"] == "killed"
    assert first["killed"] == 1

    entry = load_entry("F12")
    bank.bank(rehearse(entry, str(tmp_path / "w2"),
                       complete=scripted(entry, tmp_path, "f12")))

    second = mutation_score(bank, pool, str(tmp_path / "m2"))
    assert second["suite_valid"]
    assert second["per_mutant"]["F12"] == "killed"
    assert second["score"] > first["score"]
    assert second["per_mutant"]["F14"] == "survived"   # never rehearsed


def test_recurrence_rate_separates_fixed_from_returning(tmp_path):
    tracker = RecurrenceTracker(str(tmp_path / "recurrence.jsonl"))
    quote = {"service": "alpha", "exception_type": "TypeError",
             "request": {"path": "/quote"}}
    cancel = {"service": "alpha", "exception_type": "KeyError",
              "request": {"path": "/orders/7/cancel"}}

    # quote: incident, healed, stays quiet. cancel: incident, healed, returns.
    tracker.record_incident(quote, at=1.0)
    tracker.record_heal(quote, at=2.0)
    tracker.record_incident(cancel, at=3.0)
    tracker.record_heal(cancel, at=4.0)
    tracker.record_incident(cancel, at=5.0)

    summary = tracker.summary()
    assert summary["healed_shapes"] == 2
    assert summary["recurred_shapes"] == 1
    assert summary["recurrence_rate"] == 0.5
    # id-bearing paths collapse onto one shape
    tracker.record_incident({"service": "alpha", "exception_type": "KeyError",
                             "request": {"path": "/orders/9/cancel"}}, at=6.0)
    assert len(tracker.summary()["shapes"]) == 2
