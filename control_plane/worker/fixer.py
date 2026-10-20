"""Ask the model for a fix to a reproduced crash in a TypeScript app, and
judge each candidate with the gate: structure, the replay, types, tests.

The model sees the evidence the copy produced (the error, the request, the
frames mapped to source, and the source itself) and must answer with a
unified diff. A rejected candidate's reason goes back into the next try.
"""

from __future__ import annotations

import os
import re

MODEL = os.environ.get("KINTSUGI_MODEL", "claude-opus-5")
# conservative per-million-token prices, so the spending cap holds even if
# the real price is lower; override with KINTSUGI_PRICE_IN / _OUT
PRICE_IN = float(os.environ.get("KINTSUGI_PRICE_IN", "15"))
PRICE_OUT = float(os.environ.get("KINTSUGI_PRICE_OUT", "75"))
MAX_TRIES = 3

TEST_PATH = re.compile(r"(__tests__/|\.test\.[jt]sx?$|\.spec\.[jt]sx?$)")


def structural(diff: str) -> list[str]:
    """Reject the cheats a crash-stops-so-it-passes test admits."""
    problems = []
    for path in re.findall(r"^\+\+\+ b/(.+)$", diff, re.M):
        if TEST_PATH.search(path):
            problems.append(f"changes a test file ({path})")
    added = "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    if re.search(r"@ts-(ignore|expect-error|nocheck)|eslint-disable", added):
        problems.append("silences the type checker or linter")
    if re.search(r"catch\s*(\([^)]*\))?\s*\{\s*\}", added):
        problems.append("adds an empty catch that hides errors")
    if re.search(r"catch\s*(\([^)]*\))?\s*\{\s*return[^}]*(ok:\s*true|status:\s*200)", added):
        problems.append("turns the error into a success response")
    return problems


def numbered(src: str) -> str:
    return "\n".join(f"{i + 1:4d}  {line}" for i, line in enumerate(src.splitlines()))


def prompt(capture: dict, repro: dict, files: dict[str, str], feedback: list[str]) -> str:
    req = capture["request"]
    frames = "\n".join(f"  at {f.get('function')} ({f.get('src') or f.get('file')}:{f.get('src_line') or f.get('line')})"
                       for f in repro.get("frames", [])[:8])
    srcs = "\n\n".join(f"=== {path} ===\n{numbered(text)}" for path, text in files.items())
    fb = ("\n\nYour earlier attempts were rejected:\n" + "\n".join(f"- {x}" for x in feedback)) if feedback else ""
    return f"""A live Next.js (TypeScript) web app crashed. Kintsugi reproduced the crash in a sealed copy of the app.

Request: {req['method']} {req['path']}{('?' + req['query']) if req.get('query') else ''}
Error: {repro.get('signature', '').split('@')[0]}: {repro.get('message', '')}
Stack (mapped to source):
{frames}

Source:
{srcs}

Write the smallest correct fix for the cause of the crash. Rules: do not edit tests; do not hide the
error with an empty catch or by returning a success response; keep the route's existing behaviour
for valid requests; respond to a missing or invalid resource the way the rest of this code does.

Answer in exactly two parts:
<explanation>Two or three plain sentences for a non-programmer: what went wrong and what the fix does.</explanation>
```diff
(a unified diff with paths a/<path> and b/<path>, relative to the project root)
```{fb}"""


def ask(client, text: str) -> tuple[str, str, float]:
    msg = client.messages.create(model=MODEL, max_tokens=4000,
                                 messages=[{"role": "user", "content": text}])
    out = "".join(getattr(b, "text", "") for b in msg.content)
    cost = (msg.usage.input_tokens * PRICE_IN + msg.usage.output_tokens * PRICE_OUT) / 1e6
    m = re.search(r"```diff\n(.*?)```", out, re.S)
    e = re.search(r"<explanation>(.*?)</explanation>", out, re.S)
    return (m.group(1) if m else ""), (e.group(1).strip() if e else ""), cost


def fix(sandbox, capture: dict, repro: dict, budget_left: float, log) -> dict:
    """Up to MAX_TRIES candidates; returns the first one the gate admits."""
    import anthropic
    client = anthropic.Anthropic()
    files = {}
    for f in repro.get("frames", []):
        src = f.get("src") or ""
        if src.startswith("src/") and src not in files:
            files[src] = sandbox.source(src)
    if not files:
        return {"admitted": False, "reason": "no source file of the app appears in the crash", "candidates": [], "cost": 0.0}
    feedback, candidates, spent = [], [], 0.0
    for attempt in range(1, MAX_TRIES + 1):
        if spent >= budget_left:
            return {"admitted": False, "reason": "the spending cap was reached", "candidates": candidates, "cost": spent}
        log(f"Asking the AI for a fix (attempt {attempt} of {MAX_TRIES}).")
        diff, explanation, cost = ask(client, prompt(capture, repro, files, feedback))
        spent += cost
        cand = {"attempt": attempt, "diff": diff, "explanation": explanation, "cost": round(cost, 4)}
        candidates.append(cand)
        if not diff.strip():
            cand["rejected"] = "no diff in the answer"
            feedback.append(cand["rejected"])
            continue
        problems = structural(diff)
        if problems:
            cand["rejected"] = "; ".join(problems)
            feedback.append(cand["rejected"])
            log(f"Rejected: {cand['rejected']}.")
            continue
        ok, err = sandbox.apply(diff)
        if not ok:
            cand["rejected"] = f"the change does not apply to the code ({err.strip()[:200]})"
            feedback.append(cand["rejected"])
            log("Rejected: the change does not apply.")
            continue
        sandbox.stop()
        sandbox.start()
        after = [sandbox.replay(capture) for _ in range(5)]
        cand["replays_after"] = [{"failed": v.get("failed"), "status": v.get("status_code")} for v in after]
        if any(v.get("failed") or "harness_error" in v for v in after):
            cand["rejected"] = "the crash still happens in the copy"
            feedback.append(cand["rejected"])
            sandbox.revert(diff)
            log("Rejected: the crash still happens.")
            continue
        log("The crash no longer happens. Checking types and running the app's tests.")
        types = sandbox.typecheck()
        cand["typecheck"] = {"passed": types["passed"]}
        if not types["passed"]:
            cand["rejected"] = "the code no longer type-checks: " + types["tail"][-300:]
            feedback.append(cand["rejected"])
            sandbox.revert(diff)
            log("Rejected: type errors.")
            continue
        tests = sandbox.tests()
        cand["tests"] = tests
        if not tests["passed"]:
            cand["rejected"] = "the app's own tests fail: " + tests["summary"]
            feedback.append(cand["rejected"])
            sandbox.revert(diff)
            log("Rejected: tests fail.")
            continue
        cand["admitted"] = True
        return {"admitted": True, "candidate": cand, "candidates": candidates, "cost": spent,
                "explanation": explanation}
    return {"admitted": False, "reason": "no candidate passed every check", "candidates": candidates, "cost": spent}
