"""The overnight self-practice worker (and demo segment 1).

Runs the practice scheduler over the whole corpus with the production
model, banking rehearsals into study/practice_bank.jsonl — the bank the
console's Practice page reads and the demo opens on. The 4b exit
criterion is an overnight run banking 20+ rehearsals autonomously:

    python demo/practice_overnight.py --ticks 25
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite://")

for line in open(os.path.join(ROOT, ".env")):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

from control_plane.deploy.live import ServiceProcess, free_port
from control_plane.practice.mode import PracticeBank, PracticeScheduler
from corpus import load_corpus

BANK = os.path.join(ROOT, "study", "practice_bank.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, default=25)
    parser.add_argument("--pause", type=float, default=0.0)
    args = parser.parse_args()

    beta = ServiceProcess(os.path.join(ROOT, "services"), "beta.main:app",
                          free_port(), "b1", ROOT)
    beta.start()
    os.environ["BETA_URL"] = f"http://127.0.0.1:{beta.port}"
    try:
        bank = PracticeBank(BANK)
        scheduler = PracticeScheduler(load_corpus(), bank,
                                      tempfile.mkdtemp(prefix="practice-"))
        done = scheduler.run(args.ticks, pause=args.pause)
        for rehearsal in done:
            print(f"{rehearsal.entry_id}: {rehearsal.status}"
                  + (f" ({rehearsal.reason[:60]})" if rehearsal.reason else ""),
                  flush=True)
        counts = bank.counts()
        print(f"\nbank now holds {len(bank.all())} rehearsals across "
              f"{len(counts)} faults")
    finally:
        beta.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
