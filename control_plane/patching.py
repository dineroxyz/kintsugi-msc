"""Apply a model's diff even when its hunk headers are informal.

Models often write correct changes in a loose unified-diff form (bare "@@"
headers, wrong line counts). git rejects those outright. This applier
locates each hunk's old lines in the real file by content, replaces them with
the new lines, and returns the canonical diff git itself produces, so what
is checked, certified and deployed is always an exact, well-formed patch.
A hunk whose old lines cannot be found exactly once is a failure, never a guess.
"""

from __future__ import annotations

import os
import re
import subprocess


def _files(diff: str) -> list[tuple[str, list[list[str]]]]:
    out, path, hunks, cur = [], None, [], None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            if path is not None:
                out.append((path, hunks))
            p = line[4:].strip().split("\t")[0]
            path = p[2:] if p.startswith("b/") else p
            hunks, cur = [], None
        elif line.startswith("--- ") or line.startswith("diff --git") or line.startswith("index "):
            continue
        elif line.startswith("@@"):
            cur = []
            hunks.append(cur)
        elif cur is not None and (line[:1] in (" ", "+", "-") or line == ""):
            cur.append(line if line else " ")
    if path is not None:
        out.append((path, hunks))
    return out


def _find(lines: list[str], block: list[str]) -> int | None:
    n = len(block)
    hits = [i for i in range(len(lines) - n + 1) if lines[i:i + n] == block]
    if len(hits) == 1:
        return hits[0]
    if not hits:   # tolerate trailing-whitespace differences only
        strip = [b.rstrip() for b in block]
        hits = [i for i in range(len(lines) - n + 1) if [x.rstrip() for x in lines[i:i + n]] == strip]
        if len(hits) == 1:
            return hits[0]
    return None


def apply_loose(tree: str, diff: str) -> tuple[str | None, str]:
    """Apply `diff` to `tree` in place. Returns (canonical diff, error)."""
    if not diff.endswith("\n"):
        diff += "\n"
    if not os.path.isdir(os.path.join(tree, ".git")) and not os.path.isfile(os.path.join(tree, ".git")):
        subprocess.run(["git", "init", "-q"], cwd=tree)
    # stage the starting state, so the canonical diff is exactly this change
    subprocess.run(["git", "add", "-A"], cwd=tree, capture_output=True)
    strict = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=tree, input=diff,
                            text=True, capture_output=True)
    if strict.returncode == 0:
        return _canonical(tree), ""
    parsed = _files(diff)
    if not parsed:
        return None, "no file changes found in the answer"
    for path, hunks in parsed:
        full = os.path.join(tree, path)
        if not os.path.exists(full):
            return None, f"{path} does not exist"
        text = open(full, encoding="utf-8").read()
        lines = text.split("\n")
        for h in hunks:
            old = [l[1:] for l in h if l[:1] in (" ", "-")]
            new = [l[1:] for l in h if l[:1] in (" ", "+")]
            while old and new and old[-1] == "" and new[-1] == "":
                old.pop(); new.pop()
            at = _find(lines, old)
            if at is None:
                return None, f"could not locate a change in {path} exactly once"
            lines[at:at + len(old)] = new
        open(full, "w", encoding="utf-8").write("\n".join(lines))
    canonical = _canonical(tree)
    return (canonical, "") if canonical.strip() else (None, "the change had no effect")


def _canonical(tree: str) -> str:
    r = subprocess.run(["git", "diff", "--no-color"], cwd=tree, text=True, capture_output=True)
    return r.stdout
