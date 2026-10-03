"""Experiment data for the console: loading, classification, aggregation,
integrity checks and the test explorer's query logic.

Pure functions over the trial log the study runner writes; nothing here
renders HTML, so every number the console shows comes from one place and
is covered by tests/test_console_data.py. Missing values stay None, never 0.
"""

from __future__ import annotations

import json
import os
import statistics
from dataclasses import dataclass, field

APPROACHES = ["C0", "C1", "C2", "C3"]

APPROACH_NAME = {
    "C0": "No recovery",
    "C1": "Restart, then roll back",
    "C2": "AI fix, no safety check",
    "C3": "AI fix with safety check",
}
APPROACH_BLURB = {
    "C0": "Sees the error and does nothing. Used only for comparison.",
    "C1": "The usual approach today: restart the app, and if the error comes "
          "back, switch back to the previous version.",
    "C2": "The AI writes a fix and it goes live straight away. Nobody checks "
          "first that it really solves the problem.",
    "C3": "Kintsugi. It first makes the error happen again on purpose. The "
          "AI's fix only goes live if that error then stops, nothing else "
          "breaks, and the fix is not just hiding the error.",
}
# approaches that can put a code fix live; "bad fixes" is undefined for the rest
DEPLOYS_FIXES = {"C2", "C3"}

VARIANTS = ["C3-noguard", "C3-nomemory", "C3-noregen"]
VARIANT_NAME = {
    "C3-noguard": "Without the safety check",
    "C3-nomemory": "Same as normal, run again",
    "C3-noregen": "AI gets only one try",
}
VARIANT_CHANGE = {
    "C3-noguard": "The step that makes the error happen again before a fix is "
                  "allowed is switched off. Every bug gets a fix attempt.",
    "C3-nomemory": "Meant to switch off memory of past fixes. Memory was not "
                   "loaded in the main experiment either, so this is a "
                   "straight repeat of the normal setup.",
    "C3-noregen": "The AI gets one attempt at a fix instead of three. If the "
                  "first fix fails a check, it gives up.",
}

# outcome classification: recorded outcome -> display key and plain label
OUTCOMES = ["recovered", "refused", "escalated", "unrecovered", "false_fix",
            "harness_error", "fault_never_fired"]
OUTCOME_KEY = {
    "recovered": "fixed", "refused": "alone", "escalated": "gaveup",
    "unrecovered": "broken", "false_fix": "badfix",
    "harness_error": "error", "fault_never_fired": "nofire",
}
OUTCOME_LABEL = {
    "recovered": "Fixed",
    "refused": "Left alone",
    "escalated": "Gave up",
    "unrecovered": "Still broken",
    "false_fix": "Bad fix went live",
    "harness_error": "Test error",
    "fault_never_fired": "Did not trigger",
}
OUTCOME_MEANING = {
    "recovered": "The app works again and no more errors are coming in.",
    "refused": "It could not make the error happen again, so it could not test "
               "a fix, and left the bug for a person.",
    "escalated": "None of its fixes passed the checks, so it handed the bug "
                 "to a person.",
    "unrecovered": "Nothing fixed it, so the app kept failing.",
    "false_fix": "A fix went live but the app was still failing.",
    "harness_error": "The testing setup itself failed; the test says nothing "
                     "about the approach.",
    "fault_never_fired": "The planted bug never caused an error.",
}
EXPECTED_LABEL = {"healed": "Should be fixed", "refused": "Should be left alone"}


def classify(outcome: str) -> str:
    """Display key for a recorded outcome; unknown outcomes are 'unknown'."""
    return OUTCOME_KEY.get(outcome, "unknown")


# ------------------------------------------------------------------ loading

def load_trials(study_dir: str) -> list[dict]:
    """Every trial in the log, each tagged with a stable index `id`.
    Malformed lines are skipped and counted in `load_problems`."""
    path = os.path.join(study_dir, "trials.jsonl")
    trials: list[dict] = []
    load_trials.problems = []
    if not os.path.exists(path):
        return trials
    with open(path) as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                load_trials.problems.append(f"line {n} is not valid JSON")
                continue
            if not isinstance(row, dict) or "entry" not in row or "arm" not in row:
                load_trials.problems.append(f"line {n} lacks entry or arm")
                continue
            row.setdefault("variant", "")
            row.setdefault("seed", 1)
            row.setdefault("detail", {})
            if not isinstance(row["detail"], dict):
                row["detail"] = {}
            row["id"] = len(trials)
            trials.append(row)
    return trials


load_trials.problems = []


def main_trials(trials: list[dict]) -> list[dict]:
    return [t for t in trials if not t.get("variant")]


def ablation_trials(trials: list[dict]) -> list[dict]:
    return [t for t in trials if t.get("variant")]


# -------------------------------------------------------------- aggregation

def median_time(rows: list[dict]) -> float | None:
    """Usual time to fix: the median, over fixed tests only, of seconds from
    the first error to the moment the app works again. None if none fixed."""
    times = [t["mttr_seconds"] for t in rows
             if t.get("outcome") == "recovered"
             and isinstance(t.get("mttr_seconds"), (int, float))]
    return round(statistics.median(times), 2) if times else None


@dataclass
class Summary:
    n: int
    counts: dict = field(default_factory=dict)
    time: float | None = None

    def count(self, outcome: str) -> int:
        return self.counts.get(outcome, 0)

    def share(self, outcome: str) -> float | None:
        return self.count(outcome) / self.n if self.n else None


def summarise(rows: list[dict]) -> Summary:
    counts: dict[str, int] = {}
    for t in rows:
        counts[t.get("outcome", "")] = counts.get(t.get("outcome", ""), 0) + 1
    return Summary(n=len(rows), counts=counts, time=median_time(rows))


def by_approach(trials: list[dict]) -> dict[str, Summary]:
    rows = main_trials(trials)
    return {a: summarise([t for t in rows if t["arm"] == a])
            for a in APPROACHES if any(t["arm"] == a for t in rows)}


def by_variant(trials: list[dict]) -> dict[str, Summary]:
    rows = ablation_trials(trials)
    seen = [v for v in VARIANTS if any(t["variant"] == v for t in rows)]
    seen += sorted({t["variant"] for t in rows} - set(VARIANTS))
    return {v: summarise([t for t in rows if t["variant"] == v]) for v in seen}


def expected_of(trials: list[dict]) -> dict[str, str]:
    return {t["entry"]: t.get("expected_c3", "healed") for t in trials}


def bug_groups(trials: list[dict]) -> tuple[list[str], list[str]]:
    """Bug ids split by their pre-registered label, each group sorted."""
    expected = expected_of(main_trials(trials))
    fix = sorted(b for b, e in expected.items() if e != "refused")
    alone = sorted(b for b, e in expected.items() if e == "refused")
    return fix, alone


def cell_index(trials: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """(approach, bug) -> its main-experiment trials ordered by run."""
    cells: dict[tuple[str, str], list[dict]] = {}
    for t in main_trials(trials):
        cells.setdefault((t["arm"], t["entry"]), []).append(t)
    for rows in cells.values():
        rows.sort(key=lambda t: t.get("seed", 0))
    return cells


def correct_leave_alone(trials: list[dict]) -> dict:
    """How the gated approach's 'left alone' decisions match the labels."""
    rows = [t for t in main_trials(trials) if t["arm"] == "C3"]
    should = [t for t in rows if t.get("expected_c3") == "refused"]
    return {"should": len(should),
            "right": sum(t["outcome"] == "refused" for t in should),
            "wrongly": sum(t["outcome"] == "refused" for t in rows
                           if t.get("expected_c3") != "refused")}


# ---------------------------------------------------------------- integrity

def integrity(trials: list[dict], expect_bugs: int = 60,
              expect_runs: int = 3) -> dict:
    """Checks the shape of the main experiment. Never corrects anything:
    problems are reported so the page can say so."""
    rows = main_trials(trials)
    bugs = sorted({t["entry"] for t in rows})
    approaches = sorted({t["arm"] for t in rows})
    runs = sorted({t.get("seed") for t in rows})
    problems = list(load_trials.problems)
    if len(bugs) != expect_bugs:
        problems.append(f"{len(bugs)} bugs found, {expect_bugs} expected")
    if len(runs) != expect_runs:
        problems.append(f"{len(runs)} runs found, {expect_runs} expected")
    per_cell: dict[tuple, int] = {}
    for t in rows:
        key = (t["arm"], t["entry"], t.get("seed"))
        per_cell[key] = per_cell.get(key, 0) + 1
    dupes = [k for k, c in per_cell.items() if c > 1]
    if dupes:
        problems.append(f"{len(dupes)} bug, approach and run combinations "
                        "appear more than once")
    missing = [(a, b, r) for a in approaches for b in bugs for r in runs
               if (a, b, r) not in per_cell]
    if missing:
        problems.append(f"{len(missing)} expected tests are missing")
    expected_total = len(bugs) * len(approaches) * len(runs)
    if len(rows) != expected_total:
        problems.append(f"{len(rows)} tests found, {expected_total} expected "
                        "from bugs times approaches times runs")
    labels = expected_of(rows)
    unlabelled = [b for b in bugs if labels.get(b) not in ("healed", "refused")]
    if unlabelled:
        problems.append(f"{len(unlabelled)} bugs have no expected outcome")
    summaries = by_approach(trials)
    for a, s in summaries.items():
        if sum(s.counts.values()) != s.n:
            problems.append(f"outcome counts for {a} do not add up")
    return {"ok": not problems, "bugs": len(bugs), "approaches": len(approaches),
            "runs": len(runs), "tests": len(rows),
            "ablation_tests": len(ablation_trials(trials)),
            "problems": problems}


# ----------------------------------------------------------------- explorer

SORTS = {"bug", "result", "time", "approach"}
PER_PAGE = 25


@dataclass
class Query:
    bug: str = ""
    approach: str = ""
    outcome: str = ""
    run: str = ""
    sort: str = "bug"
    order: str = "asc"
    page: int = 1

    @classmethod
    def parse(cls, params: dict) -> "Query":
        """Lenient parsing: anything invalid falls back to the default."""
        def get(k):
            v = params.get(k, "")
            return v.strip() if isinstance(v, str) else ""
        q = cls(bug=get("bug")[:20], approach=get("approach"),
                outcome=get("outcome"), run=get("run"),
                sort=get("sort") or "bug", order=get("order") or "asc")
        if q.approach not in APPROACHES:
            q.approach = ""
        if q.outcome not in OUTCOMES:
            q.outcome = ""
        if not q.run.isdigit():
            q.run = ""
        if q.sort not in SORTS:
            q.sort = "bug"
        if q.order not in ("asc", "desc"):
            q.order = "asc"
        try:
            q.page = max(1, int(get("page") or 1))
        except ValueError:
            q.page = 1
        return q

    def active(self) -> bool:
        return bool(self.bug or self.approach or self.outcome or self.run)

    def params(self, **override) -> dict:
        d = {"bug": self.bug, "approach": self.approach,
             "outcome": self.outcome, "run": self.run,
             "sort": self.sort, "order": self.order, "page": self.page}
        d.update(override)
        return {k: v for k, v in d.items()
                if v not in ("", None) and not (k == "page" and v == 1)
                and not (k == "sort" and v == "bug")
                and not (k == "order" and v == "asc")}


_RESULT_ORDER = {o: i for i, o in enumerate(OUTCOMES)}


def explore(trials: list[dict], q: Query, per_page: int = PER_PAGE) -> dict:
    rows = main_trials(trials)
    if q.bug:
        needle = q.bug.lower()
        rows = [t for t in rows if needle in t["entry"].lower()]
    if q.approach:
        rows = [t for t in rows if t["arm"] == q.approach]
    if q.outcome:
        rows = [t for t in rows if t["outcome"] == q.outcome]
    if q.run:
        rows = [t for t in rows if str(t.get("seed")) == q.run]

    reverse = q.order == "desc"
    base = lambda t: (t["entry"], t["arm"], t.get("seed", 0))  # noqa: E731
    if q.sort == "time":
        timed = sorted((t for t in rows if t.get("mttr_seconds") is not None),
                       key=lambda t: (t["mttr_seconds"], base(t)),
                       reverse=reverse)
        # tests without a time to fix always sort last, in either direction
        rows = timed + sorted((t for t in rows if t.get("mttr_seconds") is None),
                              key=base)
    elif q.sort == "result":
        rows = sorted(rows, key=lambda t: (_RESULT_ORDER.get(t["outcome"], 99),
                                           base(t)), reverse=reverse)
    elif q.sort == "approach":
        rows = sorted(rows, key=lambda t: (t["arm"], base(t)), reverse=reverse)
    else:
        rows = sorted(rows, key=base, reverse=reverse)

    total = len(rows)
    pages = max(1, -(-total // per_page))
    page = min(q.page, pages)
    start = (page - 1) * per_page
    return {"rows": rows[start:start + per_page], "total": total,
            "page": page, "pages": pages, "start": start + 1 if total else 0,
            "end": min(start + per_page, total)}


def plain_reason(reason: str) -> str:
    """The platform's internal reason strings, in plain words."""
    import re
    r = (reason or "").lower()
    if not r:
        return ""
    if "did not recur" in r:
        return "The error would not happen again on purpose, so it was left alone."
    if "outbound call" in r:
        return "The error happened by chance, so it could not be repeated."
    if "no candidate passed" in r:
        return "None of the AI's fixes passed the checks."
    m = re.search(r"admitted on candidate (\d+)", r)
    if m:
        n = int(m.group(1))
        return "Fixed on the first try." if n == 1 else f"Fixed on try {n}."
    if "budget exhausted" in r:
        return "Too many separate errors at once; handed to a person."
    return reason[:120]
