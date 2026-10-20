"""Heal jobs for connected services: the loop on someone else's app.

A failure from a connected service opens a heal job and alerts the owner.
The heavy work (reproducing in a separate copy of the app, asking the model
for a fix, testing it, recording it) is done by a Kintsugi worker that the
owner runs next to their code, so their repository and GitHub access never
reach this server. The worker reports each stage here; this server keeps
the timeline, stores the recording, and issues the signed certificate.

Stages, in order (any may end the job):
  detected -> reproducing -> reproduced | refused
  reproduced -> fixing -> verified | gave_up
  verified -> proposed  (pull request opened)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

from sqlalchemy import ForeignKey, LargeBinary, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from control_plane.db import Base, get_session, utcnow

STAGES = ("detected", "reproducing", "reproduced", "refused", "fixing",
          "verified", "gave_up", "proposed")
FINAL = {"refused", "gave_up", "proposed"}
WORDS = {"detected": "Bug detected", "reproducing": "Reproducing in a separate copy",
         "reproduced": "Reproduced", "refused": "Left for a person",
         "fixing": "Writing and testing a fix", "verified": "Fix verified",
         "gave_up": "No safe fix found", "proposed": "Fix ready for you"}
MAX_ARTIFACT = 12 * 1024 * 1024
GENESIS = "0" * 64


class ServiceRepo(Base):
    """Where a connected service's code lives, so the worker can fix it."""
    __tablename__ = "service_repos"
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), primary_key=True)
    repo: Mapped[str] = mapped_column(String(200))          # owner/name on GitHub
    branch: Mapped[str] = mapped_column(String(100), default="main")
    framework: Mapped[str] = mapped_column(String(40), default="nextjs")


class HealJob(Base):
    __tablename__ = "heal_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), unique=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    stage: Mapped[str] = mapped_column(String(20), default="detected")
    timeline_json: Mapped[str] = mapped_column(Text, default="[]")
    explanation: Mapped[str] = mapped_column(Text, default="")
    patch: Mapped[str] = mapped_column(Text, default="")
    pr_url: Mapped[str] = mapped_column(String(300), default="")
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    certificate_json: Mapped[str] = mapped_column(Text, default="")
    seen: Mapped[bool] = mapped_column(default=False)


class Artifact(Base):
    __tablename__ = "heal_artifacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("heal_jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))            # video | log
    content_type: Mapped[str] = mapped_column(String(80))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


# ------------------------------------------------------------------ owner side

def open_job(service_id: int, incident_id: int) -> None:
    """Called on ingest; one job per incident, created on its first capture."""
    with get_session() as s:
        if s.scalar(select(HealJob).where(HealJob.incident_id == incident_id)):
            return
        s.add(HealJob(service_id=service_id, incident_id=incident_id,
                      timeline_json=json.dumps([{"stage": "detected", "at": utcnow(),
                                                 "note": "Kintsugi caught a crash."}])))
        s.commit()


def jobs_for(service_ids: list[int], limit: int = 30) -> list[HealJob]:
    if not service_ids:
        return []
    try:
        with get_session() as s:
            return list(s.scalars(select(HealJob).where(HealJob.service_id.in_(service_ids))
                                  .order_by(HealJob.id.desc()).limit(limit)))
    except Exception:
        return []


def unseen_count(service_ids: list[int]) -> int:
    return sum(1 for j in jobs_for(service_ids, 200) if not j.seen)


def get_job(job_id: int) -> HealJob | None:
    with get_session() as s:
        return s.get(HealJob, job_id)


def mark_seen(job_id: int) -> None:
    with get_session() as s:
        job = s.get(HealJob, job_id)
        if job and not job.seen:
            job.seen = True
            s.commit()


def artifacts(job_id: int) -> list[Artifact]:
    with get_session() as s:
        return list(s.scalars(select(Artifact).where(Artifact.job_id == job_id)
                              .order_by(Artifact.id)))


def get_artifact(artifact_id: int) -> Artifact | None:
    with get_session() as s:
        return s.get(Artifact, artifact_id)


def set_repo(service_id: int, repo: str, branch: str = "main") -> None:
    repo = repo.strip().removeprefix("https://github.com/").strip("/")
    if repo.count("/") != 1 or not all(p.replace("-", "").replace("_", "").replace(".", "").isalnum()
                                      for p in repo.split("/")):
        raise ValueError("Use the form owner/name, for example dineroxyz/dinero-app.")
    with get_session() as s:
        row = s.get(ServiceRepo, service_id)
        if row:
            row.repo, row.branch = repo, branch or "main"
        else:
            s.add(ServiceRepo(service_id=service_id, repo=repo, branch=branch or "main"))
        s.commit()


def repo_of(service_id: int) -> ServiceRepo | None:
    try:
        with get_session() as s:
            return s.get(ServiceRepo, service_id)
    except Exception:
        return None


# ----------------------------------------------------------------- worker side

def worker_ok(token: str) -> bool:
    want = os.environ.get("KINTSUGI_WORKER_TOKEN", "")
    return bool(want) and hmac.compare_digest(token.encode(), want.encode())


def pending(limit: int = 10) -> list[dict]:
    """Jobs waiting for the worker, with what it needs to start."""
    from control_plane.db import Capture, Incident
    out = []
    with get_session() as s:
        for job in s.scalars(select(HealJob).where(HealJob.stage == "detected")
                             .order_by(HealJob.id).limit(limit)):
            inc = s.get(Incident, job.incident_id)
            cap = s.scalar(select(Capture).where(Capture.incident_id == job.incident_id)
                           .order_by(Capture.id))
            repo = s.get(ServiceRepo, job.service_id)
            out.append({
                "job": job.id, "service_id": job.service_id, "incident": job.incident_id,
                "repo": repo.repo if repo else None, "branch": repo.branch if repo else None,
                "framework": repo.framework if repo else None,
                "release": inc.release if inc else None,
                "capture": None if cap is None else {
                    "exception_type": cap.exception_type,
                    "exception_message": cap.exception_message,
                    "release": cap.release,
                    "frames": json.loads(cap.frames_json),
                    "request": json.loads(cap.request_json),
                    "external_calls": json.loads(cap.external_calls_json)}})
    return out


def advance(job_id: int, stage: str, note: str = "", **fields) -> HealJob:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage}")
    with get_session() as s:
        job = s.get(HealJob, job_id)
        if job is None:
            raise KeyError(job_id)
        timeline = json.loads(job.timeline_json or "[]")
        timeline.append({"stage": stage, "at": utcnow(), "note": note[:2000]})
        job.timeline_json = json.dumps(timeline)
        job.stage = stage
        job.updated_at = utcnow()
        job.seen = False                      # every step is news to the owner
        for k in ("explanation", "patch", "pr_url"):
            if k in fields and fields[k] is not None:
                setattr(job, k, str(fields[k])[:200_000])
        if fields.get("cost_usd"):
            job.cost_usd = round((job.cost_usd or 0) + float(fields["cost_usd"]), 4)
        s.commit()
        s.refresh(job)
        return job


def add_artifact(job_id: int, kind: str, content_type: str, data: bytes) -> int:
    if len(data) > MAX_ARTIFACT:
        raise ValueError("artifact too large")
    with get_session() as s:
        if s.get(HealJob, job_id) is None:
            raise KeyError(job_id)
        a = Artifact(job_id=job_id, kind=kind[:40], content_type=content_type[:80], data=data)
        s.add(a)
        s.commit()
        return a.id


def total_cost() -> float:
    with get_session() as s:
        return round(sum(j.cost_usd or 0 for j in s.scalars(select(HealJob))), 4)


def certify(job_id: int, evidence: dict) -> dict:
    """Issue the job's certificate: the worker's evidence, hash-chained to
    the previous connected-service certificate and signed by this server."""
    from control_plane.certificates.pubsig import sign_hash
    from control_plane.gate.verifier import _sign
    with get_session() as s:
        job = s.get(HealJob, job_id)
        if job is None:
            raise KeyError(job_id)
        prev = s.scalar(select(HealJob).where(HealJob.certificate_json != "")
                        .order_by(HealJob.id.desc()))
        prev_hash = json.loads(prev.certificate_json)["hash"] if prev else GENESIS
        seq = len(list(s.scalars(select(HealJob.id).where(HealJob.certificate_json != ""))))
        body = {"version": 1, "chain": "connected", "sequence": seq,
                "issued_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "job": job_id,
                "incident": job.incident_id, "evidence": evidence, "prev_hash": prev_hash}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        cert = {**body, "hash": digest, "hmac": _sign({"hash": digest})}
        public = sign_hash(digest)
        if public:
            cert["ed25519"] = public
        job.certificate_json = json.dumps(cert)
        s.commit()
        return cert
