"""Incident formation: cluster raw capture events into incidents.

Chapter three, section 3.5.1. The signature is deliberately conservative —
exception type + the application-level frames (file:line) + service + release.
Two faults wrongly merged would produce a reproduction for one and a false
claim of coverage for the other; two clusters for one fault merely cost a
duplicate reproduction. Conservative therefore means: include the release,
include every app frame.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.db import Capture, Incident

# frames from these path fragments are runtime/library noise, not app identity
_LIB_MARKERS = ("/site-packages/", "/lib/python", "/uvicorn/", "/starlette/", "/fastapi/", "/anyio/")


def _app_frames(frames: list[dict]) -> list[dict]:
    app = [f for f in frames if not any(m in f["file"] for m in _LIB_MARKERS)]
    return app or frames[-3:]  # a pure-library trace still needs an identity


def signature_of(event: dict) -> str:
    """Stable cluster key for a capture event."""
    frames = _app_frames(event["frames"])
    basis = "|".join([
        event["service"],
        event["release"],
        event["exception_type"],
        *[f"{f['file']}:{f['line']}" for f in frames],
    ])
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


def ingest(session: Session, event: dict) -> tuple[Incident, Capture]:
    """Store a capture event, attaching it to its incident (creating one
    if this signature has not been seen open before)."""
    sig = signature_of(event)
    incident = session.scalar(
        select(Incident).where(Incident.signature == sig, Incident.status == "open")
    )
    if incident is None:
        incident = Incident(
            service=event["service"],
            release=event["release"],
            exception_type=event["exception_type"],
            signature=sig,
        )
        session.add(incident)
        session.flush()  # assign id before the capture references it

    capture = Capture(
        service=event["service"],
        release=event["release"],
        exception_type=event["exception_type"],
        exception_message=event["exception_message"],
        frames_json=json.dumps(event["frames"]),
        request_json=json.dumps(event["request"]),
        external_calls_json=json.dumps(event["external_calls"]),
        incident_id=incident.id,
    )
    incident.capture_count += 1
    session.add(capture)
    session.commit()
    return incident, capture
