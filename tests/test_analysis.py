"""The hand-rolled statistics against known reference values, and the
analysis pipeline end to end over a synthetic trials file."""

from __future__ import annotations

import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

spec = importlib.util.spec_from_file_location(
    "analyze", os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "study", "analyze.py"))
analyze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze)


def test_fisher_exact_matches_reference():
    # Fisher's tea-tasting-shaped table; scipy.stats.fisher_exact gives
    # p = 0.0027594 for [[1, 9], [11, 3]] two-sided
    assert analyze.fisher_exact(1, 9, 11, 3) == pytest.approx(0.00276,
                                                              abs=1e-4)
    # no association at all
    assert analyze.fisher_exact(5, 5, 5, 5) == 1.0


def test_wilcoxon_exact_small_sample():
    # all five differences positive: W+ = 15, exact two-sided p = 2/2^5
    result = analyze.wilcoxon_signed_rank([1.0, 2.0, 3.0, 4.0, 5.0])
    assert result["method"] == "exact"
    assert result["p"] == pytest.approx(0.0625)
    # zeros are dropped; fewer than 5 non-zero pairs refuses
    assert analyze.wilcoxon_signed_rank([0, 0, 1, 2, 3])["p"] is None


def test_cliffs_delta_bounds():
    assert analyze.cliffs_delta([10, 11, 12], [1, 2, 3]) == 1.0
    assert analyze.cliffs_delta([1, 2, 3], [10, 11, 12]) == -1.0
    assert analyze.cliffs_delta([1, 2], [1, 2]) == 0.0


def test_holm_is_monotone_and_bounded():
    adjusted = analyze.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adjusted["a"] == pytest.approx(0.03)      # 3 * 0.01
    assert adjusted["a"] <= adjusted["c"] <= adjusted["b"]
    assert all(p <= 1.0 for p in adjusted.values())


def test_bootstrap_ci_brackets_median_and_is_reproducible():
    values = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 30.0]
    one = analyze.bootstrap_median_ci(values)
    two = analyze.bootstrap_median_ci(values)
    assert one == two                       # seeded: replayable byte for byte
    assert one["ci95"][0] <= one["median"] <= one["ci95"][1]


def test_pipeline_over_synthetic_trials(tmp_path):
    trials = []
    for i in range(1, 9):
        entry = f"F{i:02d}"
        trials.append({"entry": entry, "arm": "C1", "outcome": "recovered",
                       "mttr_seconds": 1.0 + i * 0.01, "detail": {}})
        trials.append({"entry": entry, "arm": "C3",
                       "outcome": "recovered" if i <= 6 else "refused",
                       "mttr_seconds": 15.0 + i if i <= 6 else None,
                       "detail": {"heal_status": "deployed" if i <= 6 else "x"}})
    result = analyze.analyse(trials)
    assert result["per_arm"]["C1"]["recovery_rate"] == 1.0
    assert result["per_arm"]["C3"]["escalation_rate"] == 0.25
    comparison = result["comparisons"]["mttr:C3-vs-C1"]
    assert comparison["paired_faults"] == 6
    assert comparison["cliffs_delta"] == 1.0    # C3 slower on every pair
    assert comparison["wilcoxon"]["p"] is not None
