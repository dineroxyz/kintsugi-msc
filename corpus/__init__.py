"""The fault corpus.

Each entry is one runtime fault expressed as a set of source mutations
against the CLEAN service tree, together with the ground-truth fix (the
reverse of those mutations), the HTTP requests that surface the fault, and
provenance for where the fault pattern comes from. Entries live as JSON
files under corpus/faults/, one file per fault, so the corpus is data, not
code.

The discriminating test demanded by the study design is not stored here:
it is the reproduction the platform itself compiles from the capture. What
the corpus guarantees is narrower and checkable: the fault injects into a
clean tree, fires under its trigger, and the true fix silences it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

CORPUS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faults")


class CorpusError(RuntimeError):
    pass


@dataclass
class Mutation:
    file: str          # path relative to the service tree
    find: str          # exact source text in the clean tree, unique in file
    replace: str       # the faulty text


@dataclass
class CorpusEntry:
    id: str
    title: str
    pattern: str                    # short name of the fault family
    provenance: dict                # origin + what real-world shape it models
    service: str                    # which managed service it targets
    deterministic: bool
    stateful: bool                  # needs prior requests to build state
    mutations: list[Mutation]
    trigger: dict                   # the request that surfaces the fault
    setup: list[dict] = field(default_factory=list)   # requests run first
    expected_exception: str = ""
    expected_c3: str = "healed"     # "healed" | "refused" under the gated arm
    notes: str = ""

    @staticmethod
    def from_file(path: str) -> "CorpusEntry":
        with open(path) as fh:
            raw = json.load(fh)
        raw["mutations"] = [Mutation(**m) for m in raw["mutations"]]
        return CorpusEntry(**raw)


def load_corpus(root: str = CORPUS_ROOT) -> list[CorpusEntry]:
    entries = [CorpusEntry.from_file(os.path.join(root, name))
               for name in sorted(os.listdir(root)) if name.endswith(".json")]
    seen: set[str] = set()
    for entry in entries:
        if entry.id in seen:
            raise CorpusError(f"duplicate corpus id {entry.id}")
        seen.add(entry.id)
    return entries


def load_entry(entry_id: str, root: str = CORPUS_ROOT) -> CorpusEntry:
    for entry in load_corpus(root):
        if entry.id == entry_id:
            return entry
    raise CorpusError(f"no corpus entry {entry_id}")
