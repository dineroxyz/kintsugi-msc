"""The console's data layer: classification, aggregation, integrity and
the test explorer. Two kinds of test: synthetic logs that pin each rule
down, and the real main-study log, which must reconcile with the committed
analysis."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from console import data as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "study", "main")


def trial(entry="F01", arm="C3", seed=1, outcome="recovered", mttr=10.0,
          expected="healed", variant=""):
    return {"entry": entry, "arm": arm, "seed": seed, "outcome": outcome,
            "mttr_seconds": mttr if outcome == "recovered" else None,
            "expected_c3": expected, "variant": variant, "detail": {},
            "timings": {}}


def write_log(tmp_path, rows):
    with open(tmp_path / "trials.jsonl", "w") as fh:
        for r in rows:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    return D.load_trials(str(tmp_path))


def full_grid(bugs=("F01", "F02"), runs=(1, 2, 3)):
    rows = []
    for b in bugs:
        for a in D.APPROACHES:
            for r in runs:
                rows.append(trial(b, a, r, "recovered" if a != "C0" else "unrecovered"))
    return rows


# ---------------------------------------------------------- classification

def test_every_recorded_outcome_has_a_key_label_and_meaning():
    for o in D.OUTCOMES:
        assert D.classify(o) != "unknown"
        assert D.OUTCOME_LABEL[o] and D.OUTCOME_MEANING[o]


def test_unknown_outcome_is_not_silently_mapped():
    assert D.classify("something_new") == "unknown"


# ------------------------------------------------------------- aggregation

def test_median_time_counts_fixed_tests_only_and_none_when_nothing_fixed():
    rows = [trial(mttr=4.0), trial(mttr=8.0), trial(mttr=100.0),
            trial(outcome="escalated")]
    assert D.median_time(rows) == 8.0
    assert D.median_time([trial(outcome="unrecovered")]) is None


def test_summary_counts_add_up_and_missing_time_is_none_not_zero():
    s = D.summarise([trial(outcome="unrecovered"), trial(outcome="unrecovered")])
    assert s.n == 2 and s.count("unrecovered") == 2 and s.count("recovered") == 0
    assert s.time is None


def test_main_and_ablation_trials_are_kept_apart():
    rows = [trial(), trial(variant="C3-noregen")]
    assert len(D.main_trials(rows)) == 1 and len(D.ablation_trials(rows)) == 1
    assert D.by_approach(rows)["C3"].n == 1
    assert D.by_variant(rows)["C3-noregen"].n == 1


def test_leave_alone_decisions_are_scored_against_labels():
    rows = [trial("F02", outcome="refused", expected="refused"),
            trial("F03", outcome="refused", expected="healed"),
            trial("F04", outcome="recovered", expected="refused")]
    assert D.correct_leave_alone(rows) == {"should": 2, "right": 1, "wrongly": 1}


# --------------------------------------------------------------- integrity

def test_integrity_passes_a_complete_grid(tmp_path):
    rows = write_log(tmp_path, full_grid())
    check = D.integrity(rows, expect_bugs=2, expect_runs=3)
    assert check["ok"], check["problems"]
    assert check["tests"] == 2 * 4 * 3


def test_integrity_reports_missing_and_duplicate_tests(tmp_path):
    rows = full_grid()
    rows.pop()                                  # one test missing
    rows.append(dict(rows[0]))                  # one duplicated
    check = D.integrity(write_log(tmp_path, rows), expect_bugs=2, expect_runs=3)
    assert not check["ok"]
    text = " ".join(check["problems"])
    assert "missing" in text and "more than once" in text


def test_malformed_lines_are_skipped_and_reported(tmp_path):
    rows = write_log(tmp_path, full_grid() + ["{not json", '{"entry": "X"}'])
    assert len(rows) == 24
    check = D.integrity(rows, expect_bugs=2, expect_runs=3)
    assert not check["ok"] and any("JSON" in p for p in check["problems"])


# ---------------------------------------------------------------- explorer

@pytest.fixture()
def grid(tmp_path):
    rows = full_grid(bugs=("F01", "F02", "M10"))
    rows[5]["mttr_seconds"] = 99.0
    return write_log(tmp_path, rows)


def test_filter_by_bug_is_case_insensitive_substring(grid):
    res = D.explore(grid, D.Query.parse({"bug": "m1"}))
    assert res["total"] == 12 and {t["entry"] for t in res["rows"]} == {"M10"}


def test_filters_combine(grid):
    res = D.explore(grid, D.Query.parse({"approach": "C1", "run": "2"}))
    assert res["total"] == 3
    assert all(t["arm"] == "C1" and t["seed"] == 2 for t in res["rows"])
    res = D.explore(grid, D.Query.parse({"outcome": "unrecovered"}))
    assert res["total"] == 9


def test_sort_by_time_puts_untimed_tests_last_in_both_directions(grid):
    for order in ("asc", "desc"):
        rows = D.explore(grid, D.Query.parse({"sort": "time", "order": order}),
                         per_page=100)["rows"]
        times = [t["mttr_seconds"] for t in rows]
        first_none = times.index(None)
        assert all(t is None for t in times[first_none:])
        timed = times[:first_none]
        assert timed == sorted(timed, reverse=(order == "desc"))
    top = D.explore(grid, D.Query.parse({"sort": "time", "order": "desc"}))["rows"][0]
    assert top["mttr_seconds"] == 99.0


def test_pagination_is_bounded_and_counts_are_honest(grid):
    res = D.explore(grid, D.Query.parse({"page": "999"}), per_page=10)
    assert res["page"] == res["pages"] == 4
    assert res["start"] == 31 and res["end"] == 36 and len(res["rows"]) == 6


def test_invalid_parameters_fall_back_to_defaults():
    q = D.Query.parse({"approach": "C9", "outcome": "<script>", "run": "x",
                       "sort": "drop table", "order": "sideways", "page": "-3"})
    assert (q.approach, q.outcome, q.run, q.sort, q.order, q.page) == \
        ("", "", "", "bug", "asc", 1)


def test_query_params_omit_defaults_for_clean_links():
    assert D.Query.parse({}).params() == {}
    assert D.Query.parse({"approach": "C3"}).params(page=2) == \
        {"approach": "C3", "page": 2}


def test_empty_filter_result():
    res = D.explore([trial()], D.Query.parse({"bug": "ZZZ"}))
    assert res["total"] == 0 and res["rows"] == [] and res["start"] == 0


# ----------------------------------------------- the real main-study log

needs_study = pytest.mark.skipif(
    not os.path.exists(os.path.join(MAIN, "trials.jsonl")),
    reason="main study log not present")


@needs_study
def test_main_study_has_60_bugs_4_approaches_3_runs_720_tests():
    check = D.integrity(D.load_trials(MAIN))
    assert check["ok"], check["problems"]
    assert (check["bugs"], check["approaches"], check["runs"], check["tests"]) == \
        (60, 4, 3, 720)
    assert check["ablation_tests"] == 540


@needs_study
def test_console_numbers_reconcile_with_the_committed_analysis():
    trials = D.load_trials(MAIN)
    analysis = json.load(open(os.path.join(MAIN, "analysis.json")))
    for arm, s in D.by_approach(trials).items():
        assert s.counts == analysis["per_arm"][arm]["outcomes"]
        expected_mttr = analysis["per_arm"][arm]["mttr"]
        if expected_mttr is None:
            assert s.time is None
        else:
            assert abs(s.time - expected_mttr["median"]) < 0.01
    for name, s in D.by_variant(trials).items():
        a = analysis["ablations"][name]
        assert s.n == a["n"]
        assert round(s.count("recovered") / s.n, 3) == a["recovery_rate"]


@needs_study
def test_main_study_bug_groups_match_pre_registered_labels():
    fix, alone = D.bug_groups(D.load_trials(MAIN))
    assert len(fix) == 45 and len(alone) == 15
