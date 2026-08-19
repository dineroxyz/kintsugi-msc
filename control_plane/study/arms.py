"""Study configurations C0 and C1: what recovery looks like without repair.

C0 observes and does nothing; it is the control that turns MTTR into a
measurable ceiling. C1 is the Kephart and Chess-style fixed repertoire:
restart on first sight of a failure signature, roll back to the previous
release if the same signature survives the restart. It can execute known
remediations but can never author a new repair, which is exactly the gap
the platform exists to close; a code fault therefore survives its restart
by construction, and rollback trades recovery for every other change the
release carried.

C2 (ungated repair) and C3 (gated repair) live in control_plane.loop.heal;
all four arms share the platform beneath them.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field

from control_plane.deploy.controller import DeployController
from control_plane.deploy.live import ServiceProcess


@dataclass
class ArmAction:
    t: float
    action: str
    detail: str = ""


@dataclass
class ArmReport:
    arm: str
    actions: list[ArmAction] = field(default_factory=list)

    def record(self, action: str, detail: str = "") -> None:
        self.actions.append(ArmAction(time.time(), action, detail))

    def took(self, action: str) -> bool:
        return any(a.action == action for a in self.actions)


def purge_pycache(tree: str) -> None:
    """Stale bytecode survives same-second file swaps (.pyc mtime checks
    are 1-second granular); every tree swap must drop it."""
    for dirpath, dirnames, _ in os.walk(tree):
        if "__pycache__" in dirnames:
            shutil.rmtree(os.path.join(dirpath, "__pycache__"),
                          ignore_errors=True)


class C0NoRecovery:
    """The control arm: incidents are observed, nothing is done."""

    name = "C0"

    def __init__(self):
        self.report = ArmReport(self.name)

    def on_incident(self, signature: str) -> ArmReport:
        self.report.record("observed", signature)
        return self.report


class C1Repertoire:
    """Restart on a new signature; rollback when it recurs after restart;
    escalate when the repertoire is spent."""

    name = "C1"

    def __init__(self, service: ServiceProcess, previous_release_tree: str):
        self.service = service
        self.previous_release_tree = previous_release_tree
        self.report = ArmReport(self.name)
        self._restarted: set[str] = set()
        self._rolled_back = False

    def on_incident(self, signature: str) -> ArmReport:
        if signature not in self._restarted:
            self._restarted.add(signature)
            self.service.restart()
            self.report.record("restarted", signature)
        elif not self._rolled_back:
            self._rolled_back = True
            DeployController._sync(self.previous_release_tree,
                                   self.service.tree)
            purge_pycache(self.service.tree)
            self.service.restart()
            self.report.record("rolled_back", signature)
        else:
            self.report.record("escalated", signature)
        return self.report
