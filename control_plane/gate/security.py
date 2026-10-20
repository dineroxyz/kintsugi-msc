"""The gate's security condition: a repair must not become a way in.

The model reads data an outsider controls (the captured request, and an
exception message that may echo it). Someone could therefore send a request
built to steer the repair. The model can only propose text, never run code,
but a steered patch could still pass the other conditions: the failure stops
and the tests pass. This condition limits what a repair may be, so that a
patch can do no more than mend the failure it was written for.

Rules (each errs towards rejection; a rejected candidate is retried):
  scope      only files in the failure's own application frames may change
  size       at most MAX_ADDED added lines
  imports    no new imports
  surface    no new routes or endpoints
  reach      no new process, network, dynamic-code or file-write calls,
             and no new literal URLs
  echo       no distinctive text copied from the untrusted request
"""

from __future__ import annotations

import os
import re

MAX_ADDED = 40

_ROUTE = re.compile(r"^\s*@\s*\w+\.(get|post|put|patch|delete|route|api_route|websocket)\b")
_IMPORT = re.compile(r"^\s*(import\s+\w|from\s+\S+\s+import\s)")
_REACH = re.compile(
    r"\b(subprocess|os\.system|os\.popen|eval\s*\(|exec\s*\(|__import__|compile\s*\(|"
    r"socket\.|urllib|requests\.|httpx\.|pickle\.loads|marshal\.loads)"
    r"|open\s*\([^)]*['\"][wa]b?['\"]")
_URL = re.compile(r"https?://")
_WORD = re.compile(r"[A-Za-z0-9_\-]{8,}")

# phrases that read as instructions to a model rather than as data
_INSTRUCTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above)|disregard|you are (now )?an?\b|"
    r"system prompt|new instructions?|as the (ai|assistant|model)|when (you )?(fix|patch|repair)|"
    r"also add|add a (new )?(route|endpoint|function|comment)|<\|.*?\|>|\[\[.*?\]\]",
    re.I)


def _added(diff: str) -> list[str]:
    return [l[1:] for l in diff.split("\n") if l.startswith("+") and not l.startswith("+++")]


def _touched(diff: str) -> list[str]:
    return [p.strip().removeprefix("b/") for p in re.findall(r"^\+\+\+ (.+)$", diff, re.M)
            if p.strip() != "/dev/null"]


def _untrusted_text(capture: dict) -> str:
    req = capture.get("request") or {}
    return " ".join(str(req.get(k) or "") for k in ("path", "query", "body")) + " " + \
        str(capture.get("exception_message") or "")


def allowed_files(capture: dict, workdir: str) -> set[str]:
    """The application files the failure passed through, relative to the tree."""
    root = os.path.realpath(workdir) + os.sep
    out = set()
    for f in capture.get("frames", []):
        path = os.path.realpath(f.get("file", ""))
        if path.startswith(root):
            out.add(os.path.relpath(path, root))
        elif "/site-packages/" not in path and "/lib/python" not in path:
            # frames recorded from another copy of the same service tree
            parts = f.get("file", "").split("/")
            for i in range(1, len(parts)):           # project-relative suffixes only
                rel = "/".join(parts[i:])
                if rel and not rel.startswith("/") and os.path.exists(os.path.join(workdir, rel)):
                    out.add(rel)
                    break
    return out


def security_check(diff: str, capture: dict, workdir: str, allow_imports: bool = False,
                   allow_file_writes: bool = False) -> dict:
    problems = []
    allowed = allowed_files(capture, workdir)
    outside = [p for p in _touched(diff) if p not in allowed]
    if outside:
        problems.append(f"changes files the failure never touched: {', '.join(outside)}")
    added = _added(diff)
    if len(added) > MAX_ADDED:
        problems.append(f"adds {len(added)} lines (limit {MAX_ADDED})")
    if not allow_imports and any(_IMPORT.match(l) for l in added):
        problems.append("adds an import")
    if any(_ROUTE.match(l) for l in added):
        problems.append("adds a route or endpoint")
    reach = _REACH if not allow_file_writes else re.compile(_REACH.pattern.split("|open")[0])
    if any(reach.search(l) for l in added):
        problems.append("adds a process, network, dynamic-code or file-write call")
    if any(_URL.search(l) for l in added):
        problems.append("adds a literal URL")
    untrusted = set(_WORD.findall(_untrusted_text(capture)))
    if untrusted:
        existing = ""
        for p in _touched(diff):
            try:
                existing += open(os.path.join(workdir, p), encoding="utf-8").read()
            except OSError:
                pass
        echoed = sorted(w for w in untrusted
                        if any(w in l for l in added) and w not in existing)
        if echoed:
            problems.append(f"copies text from the untrusted request into the code: {echoed[:3]}")
    return {"passed": not problems, "problems": problems}


def looks_like_injection(capture: dict) -> bool:
    """True when the captured request contains instruction-like text."""
    return bool(_INSTRUCTION.search(_untrusted_text(capture)))


def enabled() -> bool:
    return os.environ.get("KINTSUGI_SECURITY_GATE", "1") == "1"
