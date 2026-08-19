"""The verification gate: three conditions, all mandatory, in cost order.

Chapter three, section 3.5.4. A candidate that passes emits a signed
verification record — the only input type the deploy controller accepts,
which is how the invariant is enforced by construction. A candidate that
fails at any condition is rejected with the evidence of its failure, which
becomes part of the next diagnosis attempt's package.

Condition order is cost order: the structural check is static and instant,
the reproduction is one replay, the suite is the expensive full run.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

# The signing key exists so that "a gate verdict" is a checkable artefact,
# not a boolean a caller could fabricate. Configuration in production.
_SIGNING_KEY = os.environ.get("KINTSUGI_GATE_KEY", "dev-gate-key").encode()


@dataclass
class GateVerdict:
    admitted: bool
    conditions: dict = field(default_factory=dict)   # name -> {"passed": bool, ...}
    patched_tree: str | None = None                  # workdir with patch applied
    record: str | None = None                        # signed verification record

    def failure_report(self) -> dict:
        return {name: detail for name, detail in self.conditions.items()
                if not detail["passed"]}


def _sign(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True).encode()
    return hmac.new(_SIGNING_KEY, body, hashlib.sha256).hexdigest()


def verify_record(payload: dict, signature: str) -> bool:
    return hmac.compare_digest(_sign(payload), signature)


# ---------------------------------------------------------------- structural

_TEST_PATH = re.compile(r"^(a|b)/(tests?/|.*/tests?/|test_)", re.I)


def structural_check(diff: str, exception_type: str) -> dict:
    """Reject the degenerate repairs a test-based criterion admits: deleted
    or weakened tests, and handlers that swallow the captured exception
    instead of removing its cause. Heuristic by design; every rule errs
    toward rejection and the diagnosis may try again."""
    problems = []

    touched_files = re.findall(r"^\+\+\+ (.+)$", diff, re.M)
    for path in touched_files:
        if _TEST_PATH.match(path.strip()):
            problems.append(f"patch modifies test file {path.strip()}")

    removed = [l[1:] for l in diff.split("\n")
               if l.startswith("-") and not l.startswith("---")]
    added = [l[1:] for l in diff.split("\n")
             if l.startswith("+") and not l.startswith("+++")]

    if any("assert" in l for l in removed) and not any("assert" in l for l in added):
        problems.append("patch removes assertions without replacing them")

    added_text = "\n".join(added)
    swallow = re.search(
        r"except\s+(?:\w+\.)?(" + re.escape(exception_type) + r"|Exception|BaseException)\b"
        r"[^\n]*:\s*(?:\n\s*)?(pass|return\b|\.\.\.)",
        added_text)
    if swallow:
        problems.append(
            f"patch swallows {swallow.group(1)} instead of removing the cause")

    return {"passed": not problems, "problems": problems}


# ------------------------------------------------------------------- dynamic

def discard_patched_tree(tree: str | None) -> None:
    """Remove a tree minted by _apply_patch once nothing references it:
    rejected candidates immediately, admitted ones after their deploy."""
    if not tree:
        return
    parent = os.path.dirname(tree)
    target = parent if (os.path.basename(tree) == "tree"
                        and os.path.basename(parent).startswith(
                            "kintsugi-patched-")) else tree
    shutil.rmtree(target, ignore_errors=True)


def _apply_patch(workdir: str, diff: str) -> tuple[str | None, str]:
    """Apply the diff to a copy of the tree; the original is never touched."""
    patched = tempfile.mkdtemp(prefix="kintsugi-patched-")
    dst = os.path.join(patched, "tree")
    shutil.copytree(workdir, dst,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv"))
    if not diff.endswith("\n"):
        diff += "\n"   # git apply rejects a diff without a final newline
    proc = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"],
                          input=diff, text=True, capture_output=True, cwd=dst)
    if proc.returncode != 0:
        shutil.rmtree(patched, ignore_errors=True)
        return None, proc.stderr.strip()[:1000]
    return dst, ""


def _reproduction_passes(capture: dict, service_spec: str, tree: str) -> dict:
    from control_plane.reproduction.engine import _run_harness_once
    import json as _json

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        _json.dump(capture, f)
        path = f.name
    try:
        verdict = _run_harness_once(path, service_spec, tree, timeout=60)
    finally:
        os.unlink(path)
    return {"passed": not verdict.get("failed", True)
            and not verdict.get("harness_error"),
            "replay_verdict": verdict}


def _suite_passes(tree: str, suite_path: str | None) -> dict:
    """Run the service's own test suite in the patched tree.

    suite_path None means the service declares no suite; the gate records
    that and moves on. It never falls back to running the whole tree —
    when the tree is the platform's own repo that recurses into the gate.
    """
    if suite_path is None:
        return {"passed": True, "note": "service declares no suite"}
    target = os.path.join(tree, suite_path)
    if not os.path.exists(target):
        return {"passed": True, "note": "service has no suite"}
    env = dict(os.environ)
    env["PYTHONPATH"] = tree + os.pathsep + os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "--timeout=120"]
        if _has_timeout() else [sys.executable, "-m", "pytest", target, "-q"],
        capture_output=True, text=True, cwd=tree, env=env, timeout=600)
    return {"passed": proc.returncode in (0, 5),   # 5 = no tests collected
            "tail": proc.stdout[-1500:]}


def _has_timeout() -> bool:
    try:
        import pytest_timeout  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------- gate

def gate(capture: dict, signature: str, diff: str, service_spec: str,
         workdir: str, suite_path: str | None = "tests") -> GateVerdict:
    """Apply the three conditions to one candidate patch."""
    conditions: dict = {}

    conditions["structural"] = structural_check(diff, capture["exception_type"])
    if not conditions["structural"]["passed"]:
        return GateVerdict(False, conditions)

    tree, err = _apply_patch(workdir, diff)
    if tree is None:
        conditions["applies"] = {"passed": False, "error": err}
        return GateVerdict(False, conditions)
    conditions["applies"] = {"passed": True}

    conditions["reproduction"] = _reproduction_passes(capture, service_spec, tree)
    if not conditions["reproduction"]["passed"]:
        return GateVerdict(False, conditions, patched_tree=tree)

    conditions["suite"] = _suite_passes(tree, suite_path)
    if not conditions["suite"]["passed"]:
        return GateVerdict(False, conditions, patched_tree=tree)

    payload = {
        "signature": signature,
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "conditions": {k: v["passed"] for k, v in conditions.items()},
    }
    return GateVerdict(True, conditions, patched_tree=tree,
                       record=json.dumps({"payload": payload,
                                          "hmac": _sign(payload)}))
