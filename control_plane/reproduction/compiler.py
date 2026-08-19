"""Reproduction-to-test compiler.

Emits an admitted reproduction as a standalone pytest file with post-fix
semantics: the test FAILS while the captured fault is present and PASSES
once it is repaired. The same artefact therefore serves as the gate's
acceptance criterion and, retained afterwards, as the permanent regression
test that stops the defect returning — the mechanism behind hypothesis H5's
suite-strength claim.
"""

from __future__ import annotations

import json
import re


TEMPLATE = '''"""Kintsugi regression test, compiled from incident {incident_id}.

Reproduction signature: {signature}
Provoking request: {method} {path}

Auto-generated; the embedded capture is the evidence. Do not edit by hand.
"""

import json

CAPTURE = json.loads(r\'\'\'{capture_json}\'\'\')
SERVICE_SPEC = {service_spec!r}
SIGNATURE = {signature!r}


def test_incident_{incident_id}_stays_fixed():
    from control_plane.reproduction.harness import replay

    verdict = replay(CAPTURE, SERVICE_SPEC)

    assert not verdict.get("hermetic_violation"), (
        "replay attempted an unrecorded outbound call: " + json.dumps(verdict))
    assert not verdict["failed"], (
        "incident {incident_id} has recurred: "
        + str(verdict["exception_type"]) + " at " + str(verdict["top_frame"]))
'''


def compile_reproduction(capture: dict, service_spec: str, signature: str,
                         incident_id: int | str) -> str:
    """Render the reproduction as pytest source text."""
    req = capture["request"]
    # the id lands in a function name; anything non-identifier would be a
    # SyntaxError in the emitted test
    incident_id = re.sub(r"\W", "_", str(incident_id))
    return TEMPLATE.format(
        incident_id=incident_id,
        signature=signature,
        method=req["method"],
        path=req["path"],
        capture_json=json.dumps(capture),
        service_spec=service_spec,
    )
