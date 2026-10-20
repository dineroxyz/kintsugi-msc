"""Mine real defects from a real application's own history (no model calls).

For every commit whose subject starts with "fix" and that changes both the
application package and its tests, take the commit's application-side change
and try to reverse it on the current release (HEAD). A reversal that applies
cleanly re-introduces the original defect into today's code. The commit's
own test changes name the discriminating tests (ground truth).

    python study/real/mine.py <repo> <app_pkg> [<tests_prefix>] > candidates.jsonl
"""
from __future__ import annotations

import json
import re
import subprocess
import sys


def git(repo, *args, input=None, check=True):
    p = subprocess.run(["git", "-C", repo, *args], input=input, text=True,
                       capture_output=True)
    if check and p.returncode:
        raise RuntimeError(p.stderr)
    return p


def is_test(path: str, tests: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith(tests + "/") or "/tests/" in path or "/test/" in path \
        or name.startswith("test_") or name.endswith("_test.py")


def main(repo: str, pkg: str, tests: str = "tests") -> None:
    log = git(repo, "log", "--no-merges", "--format=%H\t%ad\t%s", "--date=short",
              "-i", "--grep=^fix").stdout.splitlines()
    for line in log:
        sha, date, subject = line.split("\t", 2)
        files = git(repo, "show", "--name-only", "--format=", sha).stdout.split()
        app = [f for f in files if f.startswith(pkg + "/") and f.endswith(".py") and not is_test(f, tests)]
        test_files = [f for f in files if f.endswith(".py") and is_test(f, tests)]
        if not app or not test_files:
            continue
        diff = git(repo, "diff", f"{sha}^", sha, "--", *app, check=False).stdout
        if not diff:
            continue
        ok = git(repo, "apply", "--check", "-R", "-", input=diff, check=False).returncode == 0
        if not ok:
            continue
        tdiff = git(repo, "diff", f"{sha}^", sha, "--", *test_files, check=False).stdout
        added = sorted(set(re.findall(r"^\+\s*(?:async\s+)?def (test_\w+)", tdiff, re.M)))
        print(json.dumps({"sha": sha, "date": date, "subject": subject,
                          "app_files": app, "test_files": test_files,
                          "tests_added": added, "fault_diff": diff}), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
