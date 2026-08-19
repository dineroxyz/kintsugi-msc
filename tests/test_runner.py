"""Trial runner: the four arms produce their defining outcomes on the same
fault, and the runner's measurements come from the traffic, not the arm.

The scripted "model" for the heal arms is the corpus's own ground-truth fix
diff, which also proves the injector's emitted diffs are gate-compatible.
A deliberately useless-but-applicable diff exercises false-fix detection.
"""

from __future__ import annotations

import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest

from corpus import load_entry
from corpus.injector import fix_diff, inject
from control_plane.study.runner import run_trial

PLATFORM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES = os.path.join(PLATFORM_ROOT, "services")


def true_fix_completion(entry, tmp_path):
    """The ground-truth fix diff, served as if a model had proposed it."""
    faulty = inject(entry, SERVICES, str(tmp_path / "for-diff"))
    diff = fix_diff(entry, faulty)
    return lambda _evidence: diff


def test_c0_observes_and_the_fault_stays_live(tmp_path):
    trial = run_trial(load_entry("F01"), "C0", str(tmp_path / "w"))
    assert trial.outcome == "unrecovered"
    assert trial.mttr_seconds is None
    assert trial.timings["first_error"] >= trial.timings["fault_deployed"]


def test_c1_recovers_by_rollback_after_restart_fails(tmp_path):
    trial = run_trial(load_entry("F01"), "C1", str(tmp_path / "w"))
    assert trial.outcome == "recovered"
    assert trial.detail["actions"] == ["restarted", "rolled_back"]
    assert trial.mttr_seconds > 0


def test_c3_gated_heal_recovers(tmp_path):
    entry = load_entry("F01")
    trial = run_trial(entry, "C3", str(tmp_path / "w"),
                      complete=true_fix_completion(entry, tmp_path))
    assert trial.outcome == "recovered", trial.detail
    assert trial.detail["heal_status"] == "deployed"
    assert trial.mttr_seconds > 0


def test_c3_refuses_stateful_fault_before_any_model_call(tmp_path):
    def must_not_be_called(_evidence):
        raise AssertionError("model was consulted despite refusal")

    trial = run_trial(load_entry("F02"), "C3", str(tmp_path / "w"),
                      complete=must_not_be_called)
    assert trial.outcome == "refused", trial.detail


def test_c2_ungated_arm_ships_a_false_fix_and_the_probe_catches_it(tmp_path):
    entry = load_entry("F01")
    faulty = inject(entry, SERVICES, str(tmp_path / "for-diff"))
    path = os.path.join(faulty, "alpha/main.py")
    before = open(path).read()
    after = before.replace('"status": "ok"', '"status": "fine"')
    useless = "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="a/alpha/main.py", tofile="b/alpha/main.py"))

    trial = run_trial(entry, "C2", str(tmp_path / "w"),
                      complete=lambda _e: useless)
    assert trial.outcome == "false_fix", trial.detail
    assert trial.detail["heal_status"] == "deployed"
