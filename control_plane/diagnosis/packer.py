"""Evidence packer: assemble what the model is allowed to see.

Chapter three, section 3.5.3. The model has no tools and no repository
access; its entire world is this package. Enriching the package is the safe
lever — widening the model's authority is not — so everything the diagnosis
could need is gathered here: the failure, the reproduction signature, the
source around every implicated frame, the release diff when available, and
the failure reports of previously rejected candidates.
"""

from __future__ import annotations

import os
import subprocess

CONTEXT_LINES = 25          # lines of source shown either side of a frame
MAX_FRAME_FILES = 4         # deepest app frames only; the model needs focus


def _read_window(path: str, line: int, workdir: str) -> str | None:
    full = path if os.path.isabs(path) else os.path.join(workdir, path)
    if not os.path.exists(full):
        # frames carry the path as seen at capture; retry relative to workdir
        rel = os.path.relpath(full, "/") if os.path.isabs(path) else path
        candidate = os.path.join(workdir, os.path.basename(rel))
        if not os.path.exists(candidate):
            return None
        full = candidate
    try:
        lines = open(full, encoding="utf-8", errors="replace").read().split("\n")
    except OSError:
        return None
    lo, hi = max(0, line - CONTEXT_LINES), min(len(lines), line + CONTEXT_LINES)
    numbered = [f"{'>> ' if i + 1 == line else '   '}{i + 1:4d} {lines[i]}"
                for i in range(lo, hi)]
    return "\n".join(numbered)


def _app_frames(frames: list[dict]) -> list[dict]:
    lib = ("/site-packages/", "/lib/python", "/uvicorn/", "/starlette/",
           "/fastapi/", "/anyio/")
    app = [f for f in frames if not any(m in f["file"] for m in lib)]
    return (app or frames)[-MAX_FRAME_FILES:]


def _release_diff(workdir: str, release: str) -> str | None:
    """The change introduced by the release serving the incident, when the
    workdir is a git tree that knows it."""
    proc = subprocess.run(
        ["git", "-C", workdir, "diff", f"{release}~1..{release}", "--stat", "-p"],
        capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout[:20000]


def pack(capture: dict, signature: str, workdir: str,
         prior_failures: list[dict] | None = None,
         memory_notes: list[str] | None = None) -> dict:
    """Build the evidence package for one diagnosis attempt."""
    frames = _app_frames(capture["frames"])
    sources = []
    for f in frames:
        window = _read_window(f["file"], f["line"], workdir)
        if window:
            sources.append({
                "file": os.path.relpath(
                    f["file"], workdir) if f["file"].startswith(workdir) else f["file"],
                "line": f["line"], "function": f["function"], "source": window,
            })

    return {
        "signature": signature,
        "exception_type": capture["exception_type"],
        "exception_message": capture["exception_message"],
        "frames": frames,
        "sources": sources,
        "request": capture["request"],
        "external_calls_summary": [
            {"method": c["method"], "url": c["url"], "status": c["status"]}
            for c in capture["external_calls"]],
        "release": capture["release"],
        "release_diff": _release_diff(workdir, capture["release"]),
        "prior_failures": prior_failures or [],
        "memory_notes": memory_notes or [],
    }
