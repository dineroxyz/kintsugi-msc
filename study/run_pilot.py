"""Pilot run (pre-registered study, section 'Study sizes'): the full
corpus, every arm, one seed. Purpose: shake out the harness. Pilot data
does not enter the main analysis.

Writes study/pilot/trials.jsonl incrementally (one line per finished
trial, so a crash loses nothing) and study/pilot/summary.json at the end.
C3 trials issue certificates into study/pilot/certificates/.

Run from the repo root with the venv active:
    python study/run_pilot.py [--arms C0,C1] [--entries F01,F02]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite://")

# credentials for the production model path, never printed
env_file = os.path.join(ROOT, ".env")
if os.path.exists(env_file):
    for line in open(env_file):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from control_plane.certificates.issuer import CertificateStore
from control_plane.study.runner import run_trial
from corpus import load_corpus

ARMS = ["C0", "C1", "C2", "C3"]
OUT = os.path.join(ROOT, "study", "pilot")


def summarise(trials: list[dict]) -> dict:
    summary: dict = {"trials": len(trials), "arms": {}}
    for arm in sorted({t["arm"] for t in trials}):
        rows = [t for t in trials if t["arm"] == arm]
        outcomes: dict[str, int] = {}
        for row in rows:
            outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
        mttrs = [r["mttr_seconds"] for r in rows
                 if r["mttr_seconds"] is not None]
        summary["arms"][arm] = {
            "outcomes": outcomes,
            "recovered": outcomes.get("recovered", 0),
            "false_fix": outcomes.get("false_fix", 0),
            "median_mttr": round(statistics.median(mttrs), 2) if mttrs else None,
            "mttr_n": len(mttrs),
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--entries", default="")
    args = parser.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    corpus = load_corpus()
    if args.entries:
        wanted = {e.strip() for e in args.entries.split(",")}
        corpus = [e for e in corpus if e.id in wanted]

    os.makedirs(OUT, exist_ok=True)
    trials_path = os.path.join(OUT, "trials.jsonl")
    certificates = CertificateStore(os.path.join(OUT, "certificates"))

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    meta = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "commit": commit, "seed": 1,
            "model": os.environ.get("KINTSUGI_MODEL", "claude-opus-5"),
            "arms": arms, "entries": [e.id for e in corpus]}
    with open(os.path.join(OUT, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)

    trials: list[dict] = []
    for entry in corpus:
        for arm in arms:
            label = f"{entry.id} x {arm}"
            workdir = tempfile.mkdtemp(prefix=f"pilot-{entry.id}-{arm}-")
            t0 = time.time()
            try:
                trial = run_trial(
                    entry, arm, workdir,
                    certificates=certificates if arm == "C3" else None)
                row = trial.to_dict()
            except Exception as exc:
                row = {"entry": entry.id, "arm": arm,
                       "outcome": "harness_error",
                       "mttr_seconds": None, "timings": {},
                       "detail": {"error": f"{type(exc).__name__}: {exc}",
                                  "trace": traceback.format_exc()[-1500:]}}
            row["wall_seconds"] = round(time.time() - t0, 1)
            row["expected_c3"] = entry.expected_c3
            trials.append(row)
            with open(trials_path, "a") as fh:
                fh.write(json.dumps(row) + "\n")
            print(f"{label}: {row['outcome']} "
                  f"(mttr={row['mttr_seconds']}, wall={row['wall_seconds']}s)",
                  flush=True)

    summary = summarise(trials)
    summary["meta"] = meta
    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(os.path.join(OUT, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary["arms"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
