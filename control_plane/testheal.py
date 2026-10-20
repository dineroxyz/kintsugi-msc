"""Heal from a failing test: a second source of reproduction.

A captured crashing request is one way to reproduce a defect; a failing test
is another, and it is how most defects are found. The rule is unchanged: the
failure must recur identically on repeated fresh runs before a model is
asked anything, and a patch is admitted only if the failure stops on repeated
runs, the surrounding tests still pass, and the structural and security
checks find nothing.

The caller supplies the project-specific pieces as functions, so the same
loop serves any Python project:
    run_tests(tree, selectors) -> (returncode, output)
    fresh_tree(name) -> path       a clean copy of the code under repair
    drop_tree(path)
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field

MAX_FILES = 3
MAX_FILE_CHARS = 24_000
MAX_ADDED = 60

SYSTEM = """You are the repair stage of Kintsugi. A test fails deterministically on the \
current code. Find the cause in the application code and write the smallest correct fix. \
Do not change tests, do not weaken checks, and do not special-case the test. \
Reply with ONE unified diff and nothing else, with paths relative to the repository root \
using a/ and b/ prefixes."""


@dataclass
class Usage:
    price_in: float = float(os.environ.get("KINTSUGI_PRICE_IN", "15"))
    price_out: float = float(os.environ.get("KINTSUGI_PRICE_OUT", "75"))
    spent: float = 0.0
    calls: int = 0
    last: dict = field(default_factory=dict)

    def add(self, msg) -> None:
        self.calls += 1
        self.spent += (msg.usage.input_tokens * self.price_in +
                       msg.usage.output_tokens * self.price_out) / 1e6


def failing_ids(output: str) -> list[str]:
    return sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", output, re.M)))


def reproduce(run_tests, tree: str, selectors: list[str], runs: int = 5) -> dict:
    """The failing tests must fail, with the same set of failures, every time."""
    seen = []
    for _ in range(runs):
        rc, out = run_tests(tree, selectors)
        if rc in (0, 5):
            return {"reproduced": False, "reason": "the tests passed on a run", "runs": len(seen) + 1}
        seen.append(tuple(failing_ids(out)))
    if len(set(seen)) > 1:
        return {"reproduced": False, "reason": "the failing tests varied between runs", "runs": runs}
    return {"reproduced": True, "failing": list(seen[0]), "output": out, "runs": runs}


def _trim_output(out: str, limit: int = 6000) -> str:
    keep = [l for l in out.splitlines() if l.startswith(("E ", ">", "FAILED", "ERROR")) or ".py:" in l]
    text = "\n".join(keep) or out
    return text[-limit:]


def candidate_files(tree: str, output: str, test_files: list[str], package: str) -> list[str]:
    """Application files in the traceback, then modules the test files import."""
    found: list[str] = []
    root = os.path.realpath(tree) + os.sep
    for path in re.findall(r"([\w./-]+\.py):\d+", output):
        full = os.path.realpath(os.path.join(tree, path)) if not os.path.isabs(path) else os.path.realpath(path)
        if full.startswith(root):
            rel = os.path.relpath(full, root)
            if rel.startswith(package) and rel not in found and "/tests/" not in f"/{rel}":
                found.append(rel)
    for tf in test_files:
        try:
            mod = ast.parse(open(os.path.join(tree, tf)).read())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(mod):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            elif isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            for name in names:
                for cand in (name.replace(".", "/") + ".py", name.replace(".", "/") + "/__init__.py"):
                    for base in ("", "src/", "src/backend/base/", "src/lfx/src/"):
                        rel = base + cand
                        if rel.startswith(package) and os.path.exists(os.path.join(tree, rel)) and rel not in found:
                            found.append(rel)
    return found[:25]


def _ask(client, usage: Usage, model: str, system: str, text: str, think: bool = True) -> str:
    import time
    # thinking is capped so it can never use up the room the answer needs
    kw = ({"thinking": {"type": "adaptive"}, "extra_body": {"output_config": {"effort": "medium"}}}
          if think else {"thinking": {"type": "disabled"}})
    for attempt in range(4):                 # temporary network errors are retried
        try:
            with client.messages.stream(model=model, max_tokens=12000 if think else 2000, system=system,
                                        messages=[{"role": "user", "content": text}], **kw) as stream:
                msg = stream.get_final_message()
            break
        except Exception as exc:
            if attempt == 3 or "rate" in str(exc).lower() and attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))
    usage.add(msg)
    usage.last = {"stop": msg.stop_reason, "blocks": [b.type for b in msg.content],
                  "in": msg.usage.input_tokens, "out": msg.usage.output_tokens}
    return "".join(getattr(b, "text", "") for b in msg.content)


def localise(client, usage, model, tree, failing, output, test_src, candidates) -> list[str]:
    listing = "\n".join(f"- {c} ({os.path.getsize(os.path.join(tree, c))} bytes)" for c in candidates)
    text = (f"Failing tests: {', '.join(failing)}\n\nTest output:\n{_trim_output(output)}\n\n"
            f"Test source:\n{test_src[:12000]}\n\nCandidate application files:\n{listing}\n\n"
            f"Reply with the paths of at most {MAX_FILES} files most likely to contain the cause, one per line, nothing else.")
    reply = _ask(client, usage, model, "You locate the cause of failing tests in a codebase.", text, think=False)
    picked = [l.strip("-* `").strip() for l in reply.splitlines()]
    return [p for p in picked if p in candidates][:MAX_FILES] or candidates[:1]


def focus(src: str, words: set[str]) -> str:
    """Whole file if small; otherwise the top-level definitions that mention
    names from the failure or the test, with line numbers kept for context."""
    if len(src) <= MAX_FILE_CHARS:
        return src
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src[:MAX_FILE_CHARS]
    lines = src.splitlines()
    head = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    keep = set(range(0, (head[-1].end_lineno if head else 0)))
    scored = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = "\n".join(lines[n.lineno - 1:n.end_lineno])
            hits = sum(1 for w in words if w in body)
            scored.append((hits, n))
    budget = MAX_FILE_CHARS
    for hits, n in sorted(scored, key=lambda x: -x[0]):
        size = sum(len(l) + 1 for l in lines[n.lineno - 1:n.end_lineno])
        if hits and size <= budget:
            keep.update(range(n.lineno - 1 - len(getattr(n, "decorator_list", [])), n.end_lineno))
            budget -= size
    out, prev = [], -2
    for i in sorted(keep):
        if i != prev + 1:
            out.append(f"... (lines {prev + 2}-{i} omitted) ...")
        out.append(lines[i])
        prev = i
    return "\n".join(out)


def propose(client, usage, model, tree, failing, output, test_src, files, feedback) -> str:
    parts = [f"Failing tests: {', '.join(failing)}", f"Test output:\n{_trim_output(output)}",
             f"Test source:\n{test_src[:12000]}"]
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", output + test_src))
    for f in files:
        src = open(os.path.join(tree, f), encoding="utf-8", errors="replace").read()
        parts.append(f"=== {f} ===\n{focus(src, words)}")
    if feedback:
        parts.append("Earlier attempts were rejected:\n" + "\n".join(f"- {x}" for x in feedback))
    reply = _ask(client, usage, model, SYSTEM, "\n\n".join(parts))
    m = re.search(r"```(?:diff|patch)?\n(.*?)```", reply, re.S)
    return (m.group(1) if m else reply).lstrip()


@dataclass
class Result:
    reproduced: bool
    healed: bool = False
    reason: str = ""
    attempts: list = field(default_factory=list)
    files: list = field(default_factory=list)
    patch: str = ""


def heal(*, run_tests, fresh_tree, drop_tree, selectors, test_files, suite, package,
         client, usage: Usage, budget: float, model: str = "claude-opus-5", tries: int = 3) -> Result:
    """Reproduce, then up to `tries` candidates through the gate."""
    import subprocess
    from control_plane.gate.security import security_check
    from control_plane.gate.verifier import structural_check

    base = fresh_tree("base")
    try:
        rep = reproduce(run_tests, base, selectors)
        if not rep["reproduced"]:
            return Result(False, reason=rep["reason"])
        test_src = "\n\n".join(open(os.path.join(base, t)).read() for t in test_files
                               if os.path.exists(os.path.join(base, t)))
        cands = candidate_files(base, rep["output"], test_files, package)
        if not cands:
            return Result(True, reason="no application file found to show the model")
        if usage.spent >= budget:
            return Result(True, reason="spending cap reached before this defect")
        files = localise(client, usage, model, base, rep["failing"], rep["output"], test_src, cands)
        res = Result(True, files=files)
        feedback: list[str] = []
        for attempt in range(1, tries + 1):
            if usage.spent >= budget:
                res.reason = "spending cap reached"
                break
            diff = propose(client, usage, model, base, rep["failing"], rep["output"], test_src, files, feedback)
            a = {"attempt": attempt, "diff": diff}
            res.attempts.append(a)
            st = structural_check(diff, "AssertionError")
            capture = {"frames": [{"file": os.path.join(base, f)} for f in files], "request": {}}
            if not diff.strip():
                a["rejected"] = "no diff in the answer"
                feedback.append("Your last answer contained no diff. Reply with the unified diff only.")
                continue
            import control_plane.gate.security as sec
            old = sec.MAX_ADDED
            sec.MAX_ADDED = MAX_ADDED
            try:
                sc = security_check(diff, capture, base)
            finally:
                sec.MAX_ADDED = old
            touched = re.findall(r"^\+\+\+ b/(.+)$", diff, re.M)
            wanted = [f for f in touched if f not in files and f in cands]
            if wanted and len(files) < MAX_FILES + 2 and st["passed"]:
                files += wanted[: MAX_FILES + 2 - len(files)]
                a["rejected"] = f"needed files it had not been shown: {', '.join(wanted)}; shown now"
                feedback.append(f"You edited {', '.join(wanted)} without seeing it. It is now included; "
                                "write the fix again against its actual content.")
                continue
            if not st["passed"] or not sc["passed"]:
                a["rejected"] = "; ".join(st["problems"] + sc["problems"])
                feedback.append(a["rejected"])
                continue
            t = fresh_tree(f"try{attempt}")
            try:
                ok = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=t,
                                    input=diff if diff.endswith("\n") else diff + "\n",
                                    text=True, capture_output=True)
                if ok.returncode:
                    a["rejected"] = "the diff does not apply: " + ok.stderr[-200:]
                    feedback.append(a["rejected"])
                    continue
                passes = sum(run_tests(t, selectors)[0] == 0 for _ in range(5))
                if passes < 5:
                    a["rejected"] = f"the failing tests pass on only {passes} of 5 runs"
                    feedback.append(a["rejected"] + ". Output:\n" + _trim_output(run_tests(t, selectors)[1], 2000))
                    continue
                rc, out = run_tests(t, suite)
                if rc not in (0, 5):
                    a["rejected"] = "other tests in the same folders fail: " + ", ".join(failing_ids(out)[:5])
                    feedback.append(a["rejected"])
                    continue
                a["admitted"] = True
                res.healed, res.patch, res.reason = True, diff, f"admitted on attempt {attempt}"
                return res
            finally:
                drop_tree(t)
        if not res.healed and not res.reason:
            res.reason = "no candidate passed every check"
        return res
    finally:
        drop_tree(base)
