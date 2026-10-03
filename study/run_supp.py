"""Supplementary study (pre-registration amendment A2): H4 and H5.

The main study's "memory off" ablation turned out to be configured
identically to full C3, because no practice bank was loaded in the main
study; H4 was therefore untested, and the hardening measurements behind
H5 were never taken. This runner closes both gaps with three resumable
stages, writing only under study/supp/:

  practice  one self-practice rehearsal of every corpus entry against a
            shadow, banked into supp/practice_bank.jsonl (fresh bank built
            on the frozen corpus; the older bank predates it)
  memory    C3 with the bank loaded, two variants x three seeds:
              C3-mem      full bank; the fault itself was rehearsed
                          (H4: recurring fault classes)
              C3-mem-loo  bank minus the entry's own rehearsal; only
                          sibling faults of the same shape can be recalled
                          (H5 clause 3: rehearsed classes, unseen faults)
            compared against the main study's C3 (no memory) as baseline
  hardening the kill matrix: the banked regression suite run against
            every corpus mutant, one pytest run per mutant, recording
            which banked test killed which mutant; mutation-score growth
            and the recurrence measure are computed from it offline

    python study/run_supp.py practice|memory|hardening|all
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "study"))

import run_main  # noqa: E402  (sets DATABASE_URL, the study gate key, .env)
from run_main import one_trial  # noqa: E402

from control_plane.certificates.issuer import CertificateStore  # noqa: E402
from control_plane.deploy.live import ServiceProcess, free_port  # noqa: E402
from control_plane.metrics.hardening import PLATFORM_ROOT, SERVICES  # noqa: E402
from control_plane.practice.mode import PracticeBank, PracticeScheduler  # noqa: E402
from corpus import load_corpus  # noqa: E402
from corpus.injector import inject  # noqa: E402

OUT = os.path.join(ROOT, "study", "supp")
os.makedirs(OUT, exist_ok=True)
BANK = os.path.join(OUT, "practice_bank.jsonl")
SEEDS = [1, 2, 3]


class LeaveOneOutBank(PracticeBank):
    """The bank with one entry's own rehearsals hidden from recall."""

    def __init__(self, path: str, exclude: str):
        super().__init__(path)
        self.exclude = exclude

    def all(self) -> list[dict]:
        return [r for r in super().all() if r["entry_id"] != self.exclude]


def stage_practice() -> None:
    corpus = load_corpus()
    bank = PracticeBank(BANK)
    todo = [e for e in corpus if e.id not in bank.counts()]
    print(f"practice: {len(corpus) - len(todo)} banked, {len(todo)} to go",
          flush=True)
    if not todo:
        return
    beta = ServiceProcess(SERVICES, "beta.main:app", free_port(), "b1", ROOT)
    beta.start()
    os.environ["BETA_URL"] = f"http://127.0.0.1:{beta.port}"
    workroot = tempfile.mkdtemp(prefix="supp-practice-")
    try:
        scheduler = PracticeScheduler(corpus, bank, workroot)
        for i in range(len(todo)):
            done = scheduler.run(1)
            for r in done:
                print(f"[{i + 1}/{len(todo)}] {r.entry_id}: {r.status}"
                      + (f" ({r.reason[:60]})" if r.reason else ""),
                      flush=True)
            shutil.rmtree(workroot, ignore_errors=True)
            os.makedirs(workroot, exist_ok=True)
    finally:
        beta.stop()
        shutil.rmtree(workroot, ignore_errors=True)


def stage_memory() -> None:
    corpus = load_corpus()
    trials_path = os.path.join(OUT, "trials.jsonl")
    done = run_main.load_done(trials_path)
    certificates = CertificateStore(os.path.join(OUT, "certificates"))
    plan = [(e, s, v) for s in SEEDS for e in corpus
            for v in ("C3-mem", "C3-mem-loo")]
    remaining = [p for p in plan if (p[0].id, "C3", p[1], p[2]) not in done]
    print(f"memory: {len(plan) - len(remaining)} done, {len(remaining)} to run",
          flush=True)
    for i, (entry, seed, variant) in enumerate(remaining, 1):
        bank = (PracticeBank(BANK) if variant == "C3-mem"
                else LeaveOneOutBank(BANK, entry.id))
        os.environ["KINTSUGI_SEED"] = str(seed)   # bookkeeping only, as in run_main
        row = one_trial(entry, "C3", seed, certificates, variant=variant,
                        practice_bank=bank)
        with open(trials_path, "a") as fh:
            fh.write(json.dumps(row) + "\n")
        print(f"[{i}/{len(remaining)}] {entry.id} {variant} s{seed}: "
              f"{row['outcome']} (cands={row['detail'].get('candidates')}, "
              f"mttr={row['mttr_seconds']})", flush=True)


def _failing_tests(tests: dict[str, str], tree: str) -> set[str] | None:
    """Run the whole banked suite on `tree`; the set of test names that
    failed (errors included). None when pytest itself could not run."""
    suite = tempfile.mkdtemp(prefix="supp-suite-")
    try:
        for name, source in tests.items():
            with open(os.path.join(suite, f"test_{name}.py"), "w") as fh:
                fh.write(source)
        xml = os.path.join(suite, "report.xml")
        env = dict(os.environ)
        env["PYTHONPATH"] = tree + os.pathsep + PLATFORM_ROOT
        subprocess.run([sys.executable, "-m", "pytest", suite, "-q",
                        "--no-header", "-p", "no:cacheprovider",
                        f"--junitxml={xml}"],
                       cwd=tree, env=env, capture_output=True, timeout=900)
        if not os.path.exists(xml):
            return None
        failed = set()
        for case in ET.parse(xml).getroot().iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                module = case.get("classname", "").split(".")[-1]
                failed.add(module.removeprefix("test_"))
        return failed
    finally:
        shutil.rmtree(suite, ignore_errors=True)


def stage_hardening() -> None:
    corpus = load_corpus()
    tests = {r["entry_id"].lower(): r["regression_test"]
             for r in PracticeBank(BANK).all() if r.get("regression_test")}
    clean_failures = _failing_tests(tests, SERVICES)
    valid = {k: v for k, v in tests.items() if k not in (clean_failures or set())}
    print(f"hardening: {len(tests)} banked tests, "
          f"{len(tests) - len(valid)} fail on the clean tree and are dropped",
          flush=True)
    matrix: dict[str, list[str]] = {}
    workroot = tempfile.mkdtemp(prefix="supp-mutants-")
    try:
        for i, entry in enumerate(corpus, 1):
            mutant = inject(entry, SERVICES,
                            os.path.join(workroot, f"mutant-{entry.id}"))
            failed = _failing_tests(valid, mutant)
            matrix[entry.id] = sorted(failed) if failed is not None else None
            shutil.rmtree(mutant, ignore_errors=True)
            print(f"[{i}/{len(corpus)}] {entry.id}: killed by "
                  f"{len(failed or [])} banked tests", flush=True)
    finally:
        shutil.rmtree(workroot, ignore_errors=True)
    with open(os.path.join(OUT, "kill_matrix.json"), "w") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "banked_tests": sorted(tests),
                   "invalid_on_clean": sorted(set(tests) - set(valid)),
                   "killed_by": matrix}, fh, indent=1)


def main() -> int:
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage in ("practice", "all"):
        stage_practice()
    if stage in ("memory", "all"):
        stage_memory()
    if stage in ("hardening", "all"):
        stage_hardening()
    print(f"supplementary stage '{stage}' complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
