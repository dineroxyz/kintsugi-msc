"""Main study (pre-registration, section 'Study sizes').

The full frozen corpus x C0-C3 x three seeds, plus the registered C3
ablations (memory off, regeneration off, guard off). Full cost accounting.

Resumable and crash-safe: every finished trial is appended to
main/trials.jsonl immediately, and a rerun skips any (entry, arm, seed,
variant) already present. Kill it and relaunch with the same command; it
continues where it stopped.

The gate key is fixed for the whole study BEFORE the first certificate is
issued (the pilot taught this: a verifier with a different key reads the
whole chain as forged). It is written to main/STUDY_GATE_KEY on first run
and reused on resume, never printed.

    python study/run_main.py [--seeds 1,2,3] [--arms C0,C1,C2,C3]
                             [--entries F01,...] [--ablations]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite://")

OUT = os.path.join(ROOT, "study", "main")
os.makedirs(OUT, exist_ok=True)

# the study-wide gate key, fixed before any certificate is issued
_key_path = os.path.join(OUT, "STUDY_GATE_KEY")
if not os.path.exists(_key_path):
    with open(_key_path, "w") as fh:
        fh.write(secrets.token_hex(24))
os.environ["KINTSUGI_GATE_KEY"] = open(_key_path).read().strip()

# model credentials, never printed
for line in open(os.path.join(ROOT, ".env")):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        if k.strip() != "KINTSUGI_GATE_KEY":
            os.environ.setdefault(k.strip(), v.strip())

from control_plane.certificates.issuer import CertificateStore
from control_plane.study.runner import run_trial
from corpus import load_corpus

ARMS = ["C0", "C1", "C2", "C3"]
# C3 ablations: which single mechanism is disabled (all run gated)
ABLATIONS = {
    "C3-nomemory": {},          # memory notes off is the default here anyway;
    "C3-noregen": {"max_candidates": 1},
    "C3-noguard": {"require_reproduction": False},
}


def load_done(path: str) -> set:
    if not os.path.exists(path):
        return set()
    done = set()
    for line in open(path):
        r = json.loads(line)
        done.add((r["entry"], r["arm"], r["seed"], r.get("variant", "")))
    return done


def one_trial(entry, arm, seed, certificates, variant="", **kw):
    workdir = tempfile.mkdtemp(prefix=f"main-{entry.id}-{arm}-s{seed}-")
    t0 = time.time()
    try:
        trial = run_trial(entry, arm, workdir,
                          certificates=certificates if arm == "C3" else None,
                          **kw)
        row = trial.to_dict()
    except Exception as exc:
        row = {"entry": entry.id, "arm": arm, "outcome": "harness_error",
               "mttr_seconds": None, "timings": {},
               "detail": {"error": f"{type(exc).__name__}: {exc}",
                          "trace": traceback.format_exc()[-1500:]}}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    row["seed"] = seed
    row["variant"] = variant
    row["wall_seconds"] = round(time.time() - t0, 1)
    row["expected_c3"] = entry.expected_c3
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--entries", default="")
    parser.add_argument("--ablations", action="store_true",
                        help="also run the C3 ablation variants")
    args = parser.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    corpus = load_corpus()
    if args.entries:
        wanted = {e.strip() for e in args.entries.split(",")}
        corpus = [e for e in corpus if e.id in wanted]

    trials_path = os.path.join(OUT, "trials.jsonl")
    done = load_done(trials_path)
    certificates = CertificateStore(os.path.join(OUT, "certificates"))

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    meta = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "commit": commit, "seeds": seeds, "arms": arms,
            "model": os.environ.get("KINTSUGI_MODEL", "claude-opus-5"),
            "corpus_size": len(corpus), "ablations": args.ablations}
    with open(os.path.join(OUT, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)

    # the work list: base arms, then C3 ablations if requested. Seeds vary
    # the model's sampling (the prompt is tagged with the seed).
    plan = [(e, a, s, "", {}) for s in seeds for e in corpus for a in arms]
    if args.ablations:
        for s in seeds:
            for e in corpus:
                for variant, kw in ABLATIONS.items():
                    plan.append((e, "C3", s, variant, kw))

    remaining = [p for p in plan
                 if (p[0].id, p[1], p[2], p[3]) not in done]
    print(f"{len(done)} trials already done, {len(remaining)} to run "
          f"({len(plan)} total)", flush=True)

    for i, (entry, arm, seed, variant, kw) in enumerate(remaining, 1):
        os.environ["KINTSUGI_SEED"] = str(seed)   # bookkeeping only: seeds are independent fresh repetitions
        row = one_trial(entry, arm, seed, certificates, variant=variant, **kw)
        with open(trials_path, "a") as fh:
            fh.write(json.dumps(row) + "\n")
        label = f"{entry.id} {arm}{'/' + variant if variant else ''} s{seed}"
        print(f"[{i}/{len(remaining)}] {label}: {row['outcome']} "
              f"(mttr={row['mttr_seconds']}, {row['wall_seconds']}s)",
              flush=True)

    print("main study complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
