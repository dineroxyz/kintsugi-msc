"""Weaker-model study (pre-registration amendment A3).

C2 (ungated) and C3 (gated) on the frozen 60-fault corpus at three seeds,
with claude-haiku-4-5-20251001 instead of the main study's model. Same
resumable protocol as run_main.py; results in study/haiku/.

    python study/run_haiku.py
"""

from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "study"))
os.environ["KINTSUGI_MODEL"] = "claude-haiku-4-5-20251001"

import run_main  # noqa: E402  (environment, the study gate key)
from run_main import load_done, one_trial  # noqa: E402

from control_plane.certificates.issuer import CertificateStore  # noqa: E402
from corpus import load_corpus  # noqa: E402

OUT = os.path.join(ROOT, "study", "haiku")
ARMS = ["C2", "C3"]
SEEDS = [1, 2, 3]


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    os.environ["KINTSUGI_MODEL"] = "claude-haiku-4-5-20251001"  # .env must not win
    corpus = load_corpus()
    path = os.path.join(OUT, "trials.jsonl")
    done = load_done(path)
    certs = CertificateStore(os.path.join(OUT, "certificates"))
    with open(os.path.join(OUT, "meta.json"), "w") as fh:
        json.dump({"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "model": os.environ["KINTSUGI_MODEL"], "arms": ARMS,
                   "seeds": SEEDS, "corpus_size": len(corpus),
                   "amendment": "A3"}, fh, indent=2)
    plan = [(e, a, s) for s in SEEDS for e in corpus for a in ARMS]
    todo = [p for p in plan if (p[0].id, p[1], p[2], "") not in done]
    print(f"{len(plan) - len(todo)} done, {len(todo)} to run", flush=True)
    for i, (entry, arm, seed) in enumerate(todo, 1):
        os.environ["KINTSUGI_SEED"] = str(seed)
        row = one_trial(entry, arm, seed, certs)
        row["model"] = os.environ["KINTSUGI_MODEL"]
        with open(path, "a") as fh:
            fh.write(json.dumps(row) + "\n")
        print(f"[{i}/{len(todo)}] {entry.id} {arm} s{seed}: {row['outcome']} "
              f"(cands={row['detail'].get('candidates')}, mttr={row['mttr_seconds']})",
              flush=True)
    print("haiku study complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
