"""Evidence store.

Chapter three, section 3.6: every row is immutable once written; corrections
are new rows. SQLite locally, Postgres on Railway — selected by DATABASE_URL.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///data/kintsugi.sqlite")
# Railway hands out both postgres:// and postgresql:// forms; either way the
# installed driver is psycopg (3), so pin the dialect explicitly
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

_kwargs: dict = {}
if DATABASE_URL.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
    if DATABASE_URL in ("sqlite://", "sqlite:///:memory:"):
        # in-memory: one shared connection, or every session sees an empty db
        from sqlalchemy.pool import StaticPool
        _kwargs["poolclass"] = StaticPool
engine = create_engine(DATABASE_URL, **_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Capture(Base):
    """One flushed failure event from a managed service, verbatim."""
    __tablename__ = "captures"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    service: Mapped[str] = mapped_column(String(120))
    release: Mapped[str] = mapped_column(String(120))
    exception_type: Mapped[str] = mapped_column(String(200))
    exception_message: Mapped[str] = mapped_column(Text)
    frames_json: Mapped[str] = mapped_column(Text)
    request_json: Mapped[str] = mapped_column(Text)
    external_calls_json: Mapped[str] = mapped_column(Text)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))

    @property
    def request(self) -> dict:
        return json.loads(self.request_json)

    @property
    def external_calls(self) -> list:
        return json.loads(self.external_calls_json)


class Incident(Base):
    """A distinct production failure: many captures, one unit of work."""
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    service: Mapped[str] = mapped_column(String(120))
    release: Mapped[str] = mapped_column(String(120))
    exception_type: Mapped[str] = mapped_column(String(200))
    signature: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    capture_count: Mapped[int] = mapped_column(default=0)


def init_db() -> None:
    if DATABASE_URL.startswith("sqlite"):
        os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
