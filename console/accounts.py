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


def delete_user(user_id: int) -> None:
    """Delete the account. Its runs stay, unlinked from anyone, so they still
    count towards the site's daily ceiling (otherwise deleting and re-creating
    accounts would get round the cost limit)."""
    from sqlalchemy import update
    with get_session() as s:
        s.execute(update(Run).where(Run.user_id == user_id).values(user_id=None))
        s.execute(delete(User).where(User.id == user_id))
        s.commit()


# -------------------------------------------------------------- allowances

def _day_start() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()


def runs_left(user_id: int) -> dict:
    """What this account, and the site as a whole, may still run today
    (a rolling 24 hours)."""
    since = _day_start()
    with get_session() as s:
        mine = s.scalar(select(func.count()).select_from(Run)
                        .where(Run.user_id == user_id, Run.started_at >= since)) or 0
        everyone = s.scalar(select(func.count()).select_from(Run)
                            .where(Run.started_at >= since)) or 0
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
