"""Fault injection: turn a clean service tree into a faulty release.

Injection is literal and checked: every mutation's `find` text must occur
exactly once in its file, or the entry is rejected, so an entry can never
silently drift out of sync with the service source. The true fix is the
exact reverse of the injection, and both directions can be emitted as
unified diffs for records and for the deploy pipeline.
"""

from __future__ import annotations

import difflib
import os
import shutil

from corpus import CorpusEntry, CorpusError


def _read(tree: str, rel: str) -> str:
    with open(os.path.join(tree, rel)) as fh:
        return fh.read()


def _write(tree: str, rel: str, text: str) -> None:
    with open(os.path.join(tree, rel), "w") as fh:
        fh.write(text)


def _apply(tree: str, entry: CorpusEntry, *, reverse: bool) -> None:
    for mutation in entry.mutations:
        old, new = (mutation.replace, mutation.find) if reverse \
            else (mutation.find, mutation.replace)
        source = _read(tree, mutation.file)
        count = source.count(old)
        if count != 1:
            direction = "fix" if reverse else "fault"
            raise CorpusError(
                f"{entry.id}: {direction} text occurs {count} times in "
                f"{mutation.file}, expected exactly 1")
        _write(tree, mutation.file, source.replace(old, new))
        # .pyc validation is 1-second-mtime granular; a rewrite in the same
        # second as a prior import would leave stale bytecode live
        shutil.rmtree(os.path.join(tree, os.path.dirname(mutation.file),
                                   "__pycache__"), ignore_errors=True)


def inject(entry: CorpusEntry, clean_tree: str, dest: str) -> str:
    """Copy the clean tree to dest and apply the entry's fault mutations."""
    if os.path.exists(dest):
        raise CorpusError(f"destination {dest} already exists")
    shutil.copytree(clean_tree, dest,
                    ignore=shutil.ignore_patterns("__pycache__", ".git"))
    _apply(dest, entry, reverse=False)
    return dest


def apply_true_fix(entry: CorpusEntry, faulty_tree: str) -> None:
    """Reverse the injection in place: the ground-truth fix."""
    _apply(faulty_tree, entry, reverse=True)


def _unified(entry: CorpusEntry, tree: str, *, reverse: bool) -> str:
    """Unified diff for the entry against `tree` (clean unless reverse)."""
    chunks = []
    for mutation in entry.mutations:
        before = _read(tree, mutation.file)
        old, new = (mutation.replace, mutation.find) if reverse \
            else (mutation.find, mutation.replace)
        after = before.replace(old, new)
        chunks.append("".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{mutation.file}", tofile=f"b/{mutation.file}")))
    return "".join(chunks)


def fault_diff(entry: CorpusEntry, clean_tree: str) -> str:
    return _unified(entry, clean_tree, reverse=False)


def fix_diff(entry: CorpusEntry, faulty_tree: str) -> str:
    return _unified(entry, faulty_tree, reverse=True)
