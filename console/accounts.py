"""Visitor accounts: sign up, sign in, and a daily allowance of real runs.

Accounts exist for one reason: a signed-in visitor may press Break it for
real on the public site, which spends model calls. So each account gets a
small daily allowance, the whole site has a daily ceiling, and every run is
recorded against the account that started it.

Stored per account: the email address, a salted scrypt hash of the password
(never the password), the time it was created, and the runs it started.
Deleting an account deletes the email and password hash and unlinks the
runs from it; the runs themselves stay, anonymous, in the site's count.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import ForeignKey, String, delete, func, select
from sqlalchemy.orm import Mapped, mapped_column

from control_plane.db import Base, get_session, utcnow

RUNS_PER_ACCOUNT_PER_DAY = int(os.environ.get("KINTSUGI_RUNS_PER_ACCOUNT", "3"))
RUNS_PER_DAY = int(os.environ.get("KINTSUGI_RUNS_PER_DAY", "20"))
SIGNUPS_PER_ADDRESS_PER_HOUR = 3
MIN_PASSWORD = 10
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
    entry: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[str] = mapped_column(String(40), default=utcnow, index=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mttr: Mapped[float | None] = mapped_column(nullable=True)
    recording: Mapped[str | None] = mapped_column(String(120), nullable=True)
    certificate: Mapped[int | None] = mapped_column(nullable=True)


# --------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + \
        base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expect = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                             n=int(n), r=int(r), p=int(p), dklen=len(expect))
    except Exception:
        return False
    return hmac.compare_digest(got, expect)


_DUMMY = hash_password(secrets.token_hex(8))   # equalises timing for unknown emails


# ---------------------------------------------------------------- accounts

def normalise(email: str) -> str:
    return (email or "").strip().lower()


def check_new(email: str, password: str) -> str | None:
    """The reason a sign-up cannot go ahead, or None."""
    if not EMAIL.match(email):
        return "Enter a valid email address."
    if len(password) < MIN_PASSWORD:
        return f"Use a password of at least {MIN_PASSWORD} characters."
    if len(password) > 200:
        return "That password is too long."
    if password.lower() in email or email.split("@")[0] in password.lower():
        return "Choose a password that does not contain your email address."
    return None


def create_user(email: str, password: str) -> User:
    email = normalise(email)
    problem = check_new(email, password)
    if problem:
        raise ValueError(problem)
    with get_session() as s:
        if s.scalar(select(User).where(User.email == email)):
            raise ValueError("An account with that email already exists. Sign in instead.")
        user = User(email=email, password_hash=hash_password(password))
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


def authenticate(email: str, password: str) -> User | None:
    email = normalise(email)
    with get_session() as s:
        user = s.scalar(select(User).where(User.email == email))
    if user is None:
        verify_password(password, _DUMMY)          # same work either way
        return None
    return user if verify_password(password, user.password_hash) else None


def get_user(email: str) -> User | None:
    with get_session() as s:
        return s.scalar(select(User).where(User.email == normalise(email)))


def delete_user(user_id: int) -> None:  # noqa: F811 (services removed below)
    """Delete the account. Its runs stay, unlinked from anyone, so they still
    count towards the site's daily ceiling (otherwise deleting and re-creating
    accounts would get round the cost limit)."""
    from sqlalchemy import update
    with get_session() as s:
        s.execute(update(Run).where(Run.user_id == user_id).values(user_id=None))
        ids = list(s.scalars(select(Service.id).where(Service.user_id == user_id)))
        _delete_heal_rows(s, ids)
        s.execute(delete(Service.__table__).where(Service.__table__.c.user_id == user_id))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


# -------------------------------------------------------------- allowances

def _day_start() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()


def unlimited_emails() -> set[str]:
    """Owner accounts with no daily allowance (KINTSUGI_UNLIMITED_EMAILS,
    comma separated)."""
    return {e.strip().lower() for e in os.environ.get("KINTSUGI_UNLIMITED_EMAILS", "").split(",")
            if e.strip()}


def is_unlimited(email: str) -> bool:
    return email.strip().lower() in unlimited_emails()


def is_admin(email: str) -> bool:
    """Owner accounts double as admins: KINTSUGI_ADMIN_EMAILS, or the
    unlimited list when that is not set."""
    raw = os.environ.get("KINTSUGI_ADMIN_EMAILS")
    admins = ({e.strip().lower() for e in raw.split(",") if e.strip()}
              if raw is not None else unlimited_emails())
    return email.strip().lower() in admins


def site_counts() -> dict:
    """Headline numbers for the admin page; zeros when the database is down."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        with get_session() as s:
            return {"users": s.scalar(select(func.count(User.id))) or 0,
                    "runs_today": s.scalar(select(func.count(Run.id))
                                           .where(Run.started_at >= since)) or 0,
                    "runs_total": s.scalar(select(func.count(Run.id))) or 0,
                    "services": s.scalar(select(func.count(Service.id))) or 0}
    except Exception:
        return {"users": 0, "runs_today": 0, "runs_total": 0, "services": 0}


def runs_left(user_id: int) -> dict:
    """What this account, and the site as a whole, may still run today
    (a rolling 24 hours). Owner accounts' runs do not use up the site's
    allowance for visitors."""
    since = _day_start()
    with get_session() as s:
        mine = s.scalar(select(func.count()).select_from(Run)
                        .where(Run.user_id == user_id, Run.started_at >= since)) or 0
        owners = unlimited_emails()
        owner_ids = (list(s.scalars(select(User.id).where(func.lower(User.email).in_(owners))))
                     if owners else [])
        q = select(func.count()).select_from(Run).where(Run.started_at >= since)
        if owner_ids:
            q = q.where((Run.user_id.is_(None)) | (Run.user_id.not_in(owner_ids)))
        everyone = s.scalar(q) or 0
    return {"account": max(0, RUNS_PER_ACCOUNT_PER_DAY - mine),
            "site": max(0, RUNS_PER_DAY - everyone),
            "per_account": RUNS_PER_ACCOUNT_PER_DAY}


def start_run(user_id: int | None, entry: str) -> int:
    with get_session() as s:
        run = Run(user_id=user_id, entry=entry)
        s.add(run)
        s.commit()
        return run.id


def finish_run(run_id: int, *, outcome: str | None, mttr: float | None,
               recording: str | None, certificate: int | None) -> None:
    with get_session() as s:
        run = s.get(Run, run_id)
        if run is None:
            return
        run.outcome, run.mttr = outcome, mttr
        run.recording, run.certificate = recording, certificate
        s.commit()


def user_runs(user_id: int) -> list[Run]:
    with get_session() as s:
        return list(s.scalars(select(Run).where(Run.user_id == user_id)
                              .order_by(Run.id.desc()).limit(50)))


# ------------------------------------------------- sign-up rate limiting

_signups: dict[str, list[float]] = {}


def signup_allowed(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _signups.get(ip, []) if now - t < 3600]
    _signups[ip] = recent
    return len(recent) < SIGNUPS_PER_ADDRESS_PER_HOUR


def record_signup(ip: str) -> None:
    _signups.setdefault(ip, []).append(time.time())


# ------------------------------------------------- connected services

class Service(Base):
    """A visitor's own service, sending its failures to Kintsugi."""
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


MAX_SERVICES = 5
SERVICE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def service_tag(service: Service) -> str:
    """How a connected service's incidents are labelled in the store."""
    return f"svc{service.id}-{service.name}"


def create_service(user_id: int, name: str) -> tuple[Service, str]:
    """A new connected service and its ingest key (returned once, stored hashed)."""
    name = (name or "").strip().lower()
    if not SERVICE_NAME.match(name):
        raise ValueError("Use a short name of lower-case letters, numbers and dashes.")
    with get_session() as s:
        count = s.scalar(select(func.count()).select_from(Service)
                         .where(Service.user_id == user_id)) or 0
        if count >= MAX_SERVICES:
            raise ValueError(f"An account can connect up to {MAX_SERVICES} services.")
        key = "ks_" + secrets.token_urlsafe(24)
        svc = Service(user_id=user_id, name=name, key_hash=_key_hash(key))
        s.add(svc)
        s.commit()
        s.refresh(svc)
        return svc, key


def service_by_id(service_id: int) -> Service | None:
    with get_session() as s:
        return s.get(Service, service_id)


def service_by_key(key: str) -> Service | None:
    if not key or not key.startswith("ks_") or len(key) > 80:
        return None
    with get_session() as s:
        return s.scalar(select(Service).where(Service.key_hash == _key_hash(key)))


def user_services(user_id: int) -> list[Service]:
    with get_session() as s:
        return list(s.scalars(select(Service).where(Service.user_id == user_id)
                              .order_by(Service.id)))


def _delete_heal_rows(s, service_ids: list[int]) -> None:
    """A service's alerts, recordings and repository link go with it."""
    if not service_ids:
        return
    from console.heal import Artifact, HealJob, ServiceRepo
    jobs = list(s.scalars(select(HealJob.id).where(HealJob.service_id.in_(service_ids))))
    if jobs:
        s.execute(delete(Artifact).where(Artifact.job_id.in_(jobs)))
        s.execute(delete(HealJob).where(HealJob.id.in_(jobs)))
    s.execute(delete(ServiceRepo).where(ServiceRepo.service_id.in_(service_ids)))


def delete_service(user_id: int, service_id: int) -> None:
    with get_session() as s:
        if s.scalar(select(Service.id).where(Service.id == service_id,
                                             Service.user_id == user_id)) is None:
            return
        _delete_heal_rows(s, [service_id])
        s.execute(delete(Service).where(Service.id == service_id,
                                        Service.user_id == user_id))
        s.commit()


def service_incidents(service: Service) -> list:
    from control_plane.db import Incident
    with get_session() as s:
        return list(s.scalars(select(Incident).where(Incident.service == service_tag(service))
                              .order_by(Incident.id.desc()).limit(50)))


_ingest_times: dict[int, list[float]] = {}
INGEST_PER_MINUTE = 120


def ingest_allowed(service_id: int) -> bool:
    now = time.time()
    recent = [t for t in _ingest_times.get(service_id, []) if now - t < 60]
    if len(recent) >= INGEST_PER_MINUTE:
        _ingest_times[service_id] = recent
        return False
    recent.append(now)
    _ingest_times[service_id] = recent
    return True


def recent_runs(hours: int = 24, limit: int = 40) -> list[Run]:
    """Recent runs; empty when the database is unavailable, so the pages that
    show them still render."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        with get_session() as s:
            return list(s.scalars(select(Run).where(Run.started_at >= since)
                                  .order_by(Run.id.desc()).limit(limit)))
    except Exception:
        return []


def change_password(user_id: int, current: str, new: str) -> None:
    """Replace the password after re-checking the current one."""
    with get_session() as s:
        user = s.get(User, user_id)
        if user is None or not verify_password(current, user.password_hash):
            raise ValueError("Your current password is not right.")
        problem = check_new(user.email, new)
        if problem:
            raise ValueError(problem)
        user.password_hash = hash_password(new)
        s.commit()
