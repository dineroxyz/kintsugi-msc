"""Mine fault candidates from OSS fix-commit history.

The corpus needs faults with real-world provenance. This tool sweeps the
commit history of named Python projects for fix commits whose messages
name a concrete runtime failure (TypeError, KeyError, race, ...), pulls
each commit's patch, and keeps single-file, small, code-only changes: the
shape that transplants onto the managed services as an injectable entry.

Output is corpus/mining/candidates.jsonl, one candidate per line with the
upstream repo, sha, URL, message, matched failure class, and patch. A
human (or a later session) turns a candidate into a corpus entry by
transplanting the fault pattern into alpha or beta with provenance:

    "provenance": {"origin": "mined", "source": "<commit url>",
                   "upstream_repo": "...", "modeled_on": "..."}

Usage (GITHUB_TOKEN in the environment raises the rate limit):
    python corpus/mine_candidates.py [--repos owner/name,...] [--per-repo 15]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "corpus", "mining", "candidates.jsonl")

DEFAULT_REPOS = [
    "pallets/flask", "pallets/werkzeug", "encode/httpx", "encode/starlette",
    "fastapi/fastapi", "psf/requests", "sqlalchemy/sqlalchemy",
    "python-pillow/Pillow", "boto/boto3", "celery/celery",
]

FAILURE_CLASSES = {
    "TypeError": r"\bTypeError\b",
    "KeyError": r"\bKeyError\b",
    "AttributeError": r"\bAttributeError\b",
    "IndexError": r"\bIndexError\b",
    "ValueError": r"\bValueError\b",
    "ZeroDivisionError": r"\bZeroDivisionError\b",
    "UnboundLocalError": r"\bUnboundLocalError\b",
    "race": r"\brace(?:\s+condition)?\b|\bTOCTOU\b|\bdeadlock\b",
    "NoneType": r"\bNone(?:Type)?\b.*\b(?:not|has no)\b",
}

FIX_HINT = re.compile(r"\b(fix|fixes|fixed|prevent|crash|regression|revert)\b",
                      re.I)


def classify(message: str) -> str | None:
    if not FIX_HINT.search(message):
        return None
    for name, pattern in FAILURE_CLASSES.items():
        if re.search(pattern, message, re.I):
            return name
    return None


def mine_repo(client: httpx.Client, repo: str, per_repo: int) -> list[dict]:
    candidates: list[dict] = []
    page = 1
    while len(candidates) < per_repo and page <= 5:
        resp = client.get(f"https://api.github.com/repos/{repo}/commits",
                          params={"per_page": 100, "page": page})
        if resp.status_code != 200:
            print(f"  {repo}: HTTP {resp.status_code}, stopping", flush=True)
            break
        commits = resp.json()
        if not commits:
            break
        for commit in commits:
            message = commit["commit"]["message"]
            failure_class = classify(message)
            if failure_class is None:
                continue
            detail = client.get(commit["url"])
            if detail.status_code != 200:
                continue
            files = detail.json().get("files", [])
            code = [f for f in files if f["filename"].endswith(".py")
                    and "test" not in f["filename"].lower()]
            # transplantable shape: one code file, small patch
            if len(code) != 1 or code[0].get("changes", 999) > 40:
                continue
            candidates.append({
                "repo": repo,
                "sha": commit["sha"],
                "url": commit["html_url"],
                "message": message.splitlines()[0][:200],
                "failure_class": failure_class,
                "file": code[0]["filename"],
                "changes": code[0]["changes"],
                "patch": code[0].get("patch", "")[:4000],
                "mined_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            if len(candidates) >= per_repo:
                break
        page += 1
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos", default=",".join(DEFAULT_REPOS))
    parser.add_argument("--per-repo", type=int, default=15)
    args = parser.parse_args()

    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "kintsugi-corpus-miner"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    seen = set()
    if os.path.exists(OUT):
        seen = {json.loads(line)["sha"] for line in open(OUT)}

    total = 0
    with httpx.Client(headers=headers, timeout=30.0) as client, \
            open(OUT, "a") as sink:
        for repo in [r.strip() for r in args.repos.split(",") if r.strip()]:
            print(f"mining {repo}...", flush=True)
            for candidate in mine_repo(client, repo, args.per_repo):
                if candidate["sha"] in seen:
                    continue
                seen.add(candidate["sha"])
                sink.write(json.dumps(candidate) + "\n")
                total += 1
            print(f"  total candidates so far: {total}", flush=True)
    print(f"wrote {total} new candidates to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
