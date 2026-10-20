"""Kintsugi wiring for Mealie. Runs INSIDE Mealie's interpreter.

install(app, sink, snapdir): adds the capture agent, an identity hook that
reads the token's subject claim before redaction, and a database snapshot
taken immediately before each request (kept only for a failing request).
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.expanduser("~/kintsugi/libs"))
from kintsugi_capture import CaptureMiddleware  # noqa: E402


def identity(headers: dict) -> dict | None:
    token = ""
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
    elif "mealie.access_token=" in headers.get("cookie", ""):
        token = headers["cookie"].split("mealie.access_token=", 1)[1].split(";")[0]
    if token.count(".") != 2:
        return None
    body = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    return {k: claims[k] for k in ("sub", "long_token", "id") if k in claims} or None


def db_path() -> str:
    from mealie.core.config import get_app_settings
    return str(get_app_settings().DB_PROVIDER.db_path)


_current = {"n": 0}


class SnapshotMiddleware:
    """Copy the SQLite database before each request; keep it on failure.
    Added after the capture agent, so it is the outer layer: the copy is
    taken before the request reaches the application."""

    def __init__(self, app, snapdir: str):
        self.app, self.snapdir = app, snapdir

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        _current["n"] += 1
        n = _current["n"]
        cur = os.path.join(self.snapdir, "current.db")
        src, dst = sqlite3.connect(db_path()), sqlite3.connect(cur)
        src.backup(dst)
        dst.close()
        src.close()
        try:
            return await self.app(scope, receive, send)
        except Exception:
            shutil.copy(cur, os.path.join(self.snapdir, f"snap-{n}.db"))
            raise


def install(app, sink: str, snapdir: str | None = None) -> None:
    def deliver(event):
        event["snapshot"] = (os.path.join(snapdir, f"snap-{_current['n']}.db")
                             if snapdir else None)
        with open(sink, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    app.add_middleware(CaptureMiddleware, service="mealie", deliver=deliver,
                       identity=identity, release=os.environ.get("KINTSUGI_RELEASE", "head"))
    if snapdir:
        os.makedirs(snapdir, exist_ok=True)
        app.add_middleware(SnapshotMiddleware, snapdir=snapdir)
