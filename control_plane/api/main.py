"""Kintsugi control plane API.

Ingest is the only write path from managed services; everything else is
read surface for the console and, later, the loop stages.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from sqlalchemy import select

from control_plane.db import Capture, Incident, get_session, init_db

app = FastAPI(title="Kintsugi Control Plane")


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "component": "control-plane"}


@app.post("/ingest/event")
async def ingest_event(request: Request) -> dict:
    from control_plane.incidents.former import ingest  # local import: keeps app importable without DB

    event = await request.json()
    with get_session() as session:
        incident, capture = ingest(session, event)
        return {"incident_id": incident.id, "capture_id": capture.id,
                "capture_count": incident.capture_count}


@app.get("/incidents")
def list_incidents() -> list[dict]:
    with get_session() as session:
        rows = session.scalars(select(Incident).order_by(Incident.id.desc())).all()
        return [{
            "id": i.id, "service": i.service, "release": i.release,
            "exception_type": i.exception_type, "status": i.status,
            "capture_count": i.capture_count, "created_at": i.created_at,
        } for i in rows]


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: int) -> dict:
    with get_session() as session:
        incident = session.get(Incident, incident_id)
        captures = session.scalars(
            select(Capture).where(Capture.incident_id == incident_id).limit(5)
        ).all()
        return {
            "id": incident.id, "service": incident.service, "release": incident.release,
            "exception_type": incident.exception_type, "status": incident.status,
            "capture_count": incident.capture_count,
            "sample_captures": [{
                "id": c.id,
                "exception_message": c.exception_message,
                "request": json.loads(c.request_json),
                "external_calls": json.loads(c.external_calls_json),
                "frames": json.loads(c.frames_json),
            } for c in captures],
        }
