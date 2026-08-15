"""Diagnosis: the language model's single job.

Chapter three, section 3.5.3. The model receives an evidence package and
returns a unified diff — nothing else. It cannot execute commands, cannot
read files beyond what the packer chose, cannot deploy. The platform parses
its output as a diff and discards anything that fails to apply, so the
model's authority ends at proposing text.

The completion function is injectable: the study's configurations and the
tests supply their own, and only production uses the Anthropic client.
"""

from __future__ import annotations

import json
import os
import re

SYSTEM_PROMPT = """You are the diagnosis stage of Kintsugi, an autonomous \
software repair platform. You receive the evidence for one production \
incident: the exception, the failing frames with surrounding source, the \
provoking request, summaries of recorded outbound calls, the release diff \
when available, and the failure reports of any previously rejected patches.

Propose the minimal patch that removes the fault's cause. Address the cause, \
not the symptom: do not delete tests, weaken assertions, or wrap the failure \
in a handler that hides it — the verification gate rejects all of these.

Reply with ONE unified diff and nothing else: no prose, no code fences. \
Paths in the diff must be relative to the repository root, using a/ and b/ \
prefixes."""


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:diff|patch)?\n(.*?)```\s*$", text, re.S)
    return m.group(1) if m else text


def render_evidence(package: dict) -> str:
    """The evidence package as the model sees it."""
    parts = [
        f"INCIDENT SIGNATURE: {package['signature']}",
        f"EXCEPTION: {package['exception_type']}: {package['exception_message']}",
        f"RELEASE: {package['release']}",
        "",
        f"PROVOKING REQUEST: {package['request']['method']} "
        f"{package['request']['path']}"
        + (f"?{package['request']['query']}" if package['request'].get('query') else ""),
    ]
    if package["request"].get("body"):
        parts.append(f"REQUEST BODY: {package['request']['body'][:2000]}")
    if package["external_calls_summary"]:
        parts.append("\nOUTBOUND CALLS DURING THE REQUEST:")
        for c in package["external_calls_summary"]:
            parts.append(f"  {c['method']} {c['url']} -> {c['status']}")
    parts.append("\nFAILING FRAMES (deepest last):")
    for f in package["frames"]:
        parts.append(f"  {f['file']}:{f['line']} in {f['function']}: {f.get('code')}")
    for s in package["sources"]:
        parts.append(f"\nSOURCE {s['file']} around line {s['line']} "
                     f"(>> marks the failing line):\n{s['source']}")
    if package.get("release_diff"):
        parts.append(f"\nCHANGE INTRODUCED BY THIS RELEASE:\n{package['release_diff']}")
    for note in package.get("memory_notes", []):
        parts.append(f"\nFROM INCIDENT MEMORY: {note}")
    for i, failure in enumerate(package.get("prior_failures", []), 1):
        parts.append(f"\nPREVIOUS CANDIDATE {i} WAS REJECTED:\n"
                     f"{json.dumps(failure, indent=1)[:3000]}")
    return "\n".join(parts)


def anthropic_complete(evidence_text: str) -> str:
    """Production completion via the Anthropic API. The model is
    configuration (study arms pin their own); adaptive thinking stays on."""
    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("KINTSUGI_MODEL", "claude-opus-5")
    with client.messages.stream(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": evidence_text}],
    ) as stream:
        response = stream.get_final_message()
    return "".join(b.text for b in response.content if b.type == "text")


def propose(package: dict, complete=None) -> dict:
    """One diagnosis attempt. Returns {'diff': str, 'evidence_text': str}."""
    evidence_text = render_evidence(package)
    completion = (complete or anthropic_complete)(evidence_text)
    return {"diff": _strip_fences(completion), "evidence_text": evidence_text}
