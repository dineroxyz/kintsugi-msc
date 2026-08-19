"""Arms C0 and C1 against a live faulty release under load.

The scenario both tests share: release r2 of alpha carries corpus fault F01,
real beta behind it, deterministic load mixing healthy and faulting requests.
C0 must observe and change nothing. C1 must restart first (and the code
fault must survive the restart — the repertoire's defining limit), then roll
back to r1 and actually recover the service.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import httpx
import pytest

from corpus import load_entry
from corpus.injector import inject
from control_plane.deploy.live import ServiceProcess, free_port
from control_plane.study.arms import C0NoRecovery, C1Repertoire
from control_plane.study.loadgen import LoadGenerator

PLATFORM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES = os.path.join(PLATFORM_ROOT, "services")

PLAN = [
    {"method": "GET", "path": "/quote", "params": {"sku": "WIDGET", "code": "SAVE10"}},
    {"method": "GET", "path": "/quote", "params": {"sku": "GADGET"}},
    {"method": "GET", "path": "/quote", "params": {"sku": "WIDGET", "code": "BOGUS"}},
    {"method": "GET", "path": "/health"},
]


@pytest.fixture()
def rig(tmp_path):
    entry = load_entry("F01")
    clean = str(tmp_path / "r1")
    shutil.copytree(SERVICES, clean,
                    ignore=shutil.ignore_patterns("__pycache__", ".git"))
    live = inject(entry, SERVICES, str(tmp_path / "live"))

    beta = ServiceProcess(clean, "beta.main:app", free_port(), "b1",
                          PLATFORM_ROOT)
    beta.start()
    alpha = ServiceProcess(live, "alpha.main:app", free_port(), "r2",
                           PLATFORM_ROOT,
                           extra_env={"BETA_URL": f"http://127.0.0.1:{beta.port}"})
    alpha.start()
    load = LoadGenerator(f"http://127.0.0.1:{alpha.port}", PLAN, interval=0.01)
    load.start()
    yield {"alpha": alpha, "load": load, "clean": clean, "live": live}
    load.stop()
    alpha.stop()
    beta.stop()


def _trigger_status(port: int) -> int:
    with httpx.Client(timeout=5.0) as client:
        return client.get(f"http://127.0.0.1:{port}/quote",
                          params={"sku": "WIDGET", "code": "BOGUS"}).status_code


def test_c0_observes_and_nothing_changes(rig):
    load, alpha = rig["load"], rig["alpha"]
    assert load.wait_for_error() is not None

    tree_before = open(os.path.join(rig["live"], "alpha/main.py")).read()
    pid_before = alpha.proc.pid
    arm = C0NoRecovery()
    report = arm.on_incident("sig-F01")

    assert report.took("observed")
    time.sleep(0.5)
    assert load.errors_since(time.time() - 0.5), "C0 must leave the fault live"
    assert alpha.proc.pid == pid_before
    assert open(os.path.join(rig["live"], "alpha/main.py")).read() == tree_before


def test_c1_restarts_then_rolls_back_and_recovers(rig):
    load, alpha = rig["load"], rig["alpha"]
    assert load.wait_for_error() is not None

    arm = C1Repertoire(alpha, rig["clean"])
    pid_before = alpha.proc.pid
    arm.on_incident("sig-F01")
    assert arm.report.took("restarted")
    assert alpha.proc.pid != pid_before

    # the defining limit of the repertoire: a code fault survives a restart
    assert _trigger_status(alpha.port) == 500

    arm.on_incident("sig-F01")
    assert arm.report.took("rolled_back")
    assert _trigger_status(alpha.port) == 200

    # the recovery is visible in the traffic stream, not just to a probe
    recovered_at = time.time()
    time.sleep(0.6)
    assert not load.errors_since(recovered_at)

    # repertoire spent: a third recurrence can only escalate
    arm.on_incident("sig-F01")
    assert arm.report.took("escalated")
