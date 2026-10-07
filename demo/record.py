"""Record demo segments as timed transcripts for the fallback videos.

    python demo/record.py heal replay refusal

Runs each segment of demo/segments.py for real and saves every output line
with the second it appeared to demo/recordings/<segment>.json. The film
script (demo/film.js) replays those transcripts at their real pace and
films them, so a fallback video shows a genuine run, not a mock-up.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "demo", "recordings")
NOISE = ("Warning", "warning", "TestClient", "from starlette")

TITLES = {"heal": "Live break and heal",
          "replay": "Checking and replaying a certificate",
          "refusal": "A bug Kintsugi will not touch"}


def record(segment: str) -> str:
    os.makedirs(OUT, exist_ok=True)
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    cmd = [sys.executable, os.path.join(ROOT, "demo", "segments.py"), segment]
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    lines = []
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if any(n in line for n in NOISE):
            continue
        lines.append([round(time.time() - t0, 2), line])
        print(line, flush=True)
    proc.wait()
    if proc.returncode:
        raise SystemExit(f"segment {segment} failed with code {proc.returncode}")
    path = os.path.join(OUT, f"{segment}.json")
    with open(path, "w") as fh:
        json.dump({"segment": segment, "title": TITLES.get(segment, segment),
                   "command": f"python demo/segments.py {segment}",
                   "recorded": time.strftime("%-d %B %Y"),
                   "duration": round(time.time() - t0, 2), "lines": lines},
                  fh, indent=1)
    return path


if __name__ == "__main__":
    for seg in sys.argv[1:] or ["heal", "replay", "refusal"]:
        print(f"wrote {record(seg)}")
