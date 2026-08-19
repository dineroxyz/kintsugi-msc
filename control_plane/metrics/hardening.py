"""Hardening metrics: does the platform get harder to break as it runs?

Two measurements back hypothesis H5.

Mutation score. The banked regression tests (compiled reproductions from
rehearsals and real heals) are run as one suite against MUTANT trees. The
mutant pool is the corpus itself: every corpus entry is a fault-realistic
mutation of the clean tree, which makes the score mean something concrete —
"of the fault shapes we know can happen, how many would the banked suite
now catch before deploy?" A mutant is killed when at least one banked test
fails on it. (Divergence from the plan's mutmut, recorded for chapter four:
arbitrary operator mutants measure suite bulk; corpus mutants measure
protection against the failure classes the study is actually about.)

Recurrence. Incidents and heals are recorded under the release-independent
practice key; a recurrence is an incident whose key was already healed.
A falling recurrence rate over time is the platform banking its repairs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from control_plane.practice.mode import practice_key
from corpus import CorpusEntry
from corpus.injector import inject

PLATFORM_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
SERVICES = os.path.join(PLATFORM_ROOT, "services")


def _run_suite(test_sources: dict[str, str], tree: str,
               timeout: float = 120.0) -> bool:
    """Run the banked tests against `tree`; True when all pass."""
    suite_dir = tempfile.mkdtemp(prefix="kintsugi-banked-")
    try:
        for name, source in test_sources.items():
            with open(os.path.join(suite_dir, f"test_{name}.py"), "w") as fh:
                fh.write(source)
        env = dict(os.environ)
        env["PYTHONPATH"] = tree + os.pathsep + PLATFORM_ROOT \
            + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", suite_dir, "-q", "-x",
             "--no-header", "-p", "no:cacheprovider"],
            cwd=tree, env=env, capture_output=True, timeout=timeout)
        return proc.returncode == 0
    finally:
        shutil.rmtree(suite_dir, ignore_errors=True)


def banked_tests(bank) -> dict[str, str]:
    """The regression suite the platform has accumulated so far, newest
    rehearsal per entry winning."""
    tests: dict[str, str] = {}
    for row in bank.all():
        if row.get("regression_test"):
            tests[row["entry_id"].lower()] = row["regression_test"]
    return tests


def mutation_score(bank, corpus: list[CorpusEntry], workroot: str) -> dict:
    """Kill rate of the banked suite over the corpus mutant pool."""
    tests = banked_tests(bank)
    result = {"suite_size": len(tests), "total": len(corpus),
              "killed": 0, "per_mutant": {}, "suite_valid": None}
    if not tests:
        result["score"] = 0.0
        return result

    # validity: the banked suite must PASS on the clean tree, otherwise
    # "kills" are just a broken suite failing everywhere
    result["suite_valid"] = _run_suite(tests, SERVICES)

    for entry in corpus:
        mutant = inject(entry, SERVICES,
                        os.path.join(workroot, f"mutant-{entry.id}"))
        killed = not _run_suite(tests, mutant)
        result["per_mutant"][entry.id] = "killed" if killed else "survived"
        result["killed"] += int(killed)
    result["score"] = round(result["killed"] / result["total"], 3)
    return result


class RecurrenceTracker:
    """Append-only incident/heal log keyed by failure shape."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def _append(self, kind: str, capture: dict, at: float | None) -> None:
        row = {"kind": kind, "at": at if at is not None else time.time(),
               "key": practice_key(capture.get("service", ""),
                                   capture.get("exception_type", ""),
                                   capture.get("request", {}).get("path", ""))}
        with open(self.path, "a") as fh:
            fh.write(json.dumps(row) + "\n")

    def record_incident(self, capture: dict, at: float | None = None) -> None:
        self._append("incident", capture, at)

    def record_heal(self, capture: dict, at: float | None = None) -> None:
        self._append("heal", capture, at)

    def rows(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        return [json.loads(line) for line in open(self.path)]

    def summary(self) -> dict:
        """Per-shape counts and the headline recurrence rate: of the shapes
        that were healed, how many came back afterwards?"""
        healed_at: dict[str, float] = {}
        shapes: dict[str, dict] = {}
        recurred: set[str] = set()
        for row in sorted(self.rows(), key=lambda r: r["at"]):
            shape = shapes.setdefault(row["key"],
                                      {"incidents": 0, "heals": 0})
            if row["kind"] == "incident":
                shape["incidents"] += 1
                if row["key"] in healed_at and row["at"] > healed_at[row["key"]]:
                    recurred.add(row["key"])
            else:
                shape["heals"] += 1
                healed_at[row["key"]] = row["at"]
        healed = set(healed_at)
        return {"shapes": shapes,
                "healed_shapes": len(healed),
                "recurred_shapes": len(recurred),
                "recurrence_rate": round(len(recurred) / len(healed), 3)
                if healed else 0.0}
