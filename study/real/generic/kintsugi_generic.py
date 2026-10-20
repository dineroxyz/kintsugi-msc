"""pytest plugin: attach Kintsugi's capture to ANY Starlette/FastAPI app.

Every application's entry point (Starlette.__call__, which FastAPI's ends in) is wrapped, so
an unhandled exception that leaves the application is recorded to the file
named by KINTSUGI_SINK, whatever code created the app. Standard library only,
so it runs inside any project's environment.
"""
import json
import os
import traceback


def _failed_snapshot(sink, snap):
    """Keep a snapshot only if a captured failure refers to it."""
    try:
        return snap in open(sink, encoding="utf-8").read()
    except OSError:
        return False


def _install():
    sink = os.environ.get("KINTSUGI_SINK")
    if not sink:
        return
    try:
        import starlette.applications as sa
    except ImportError:
        return
    if getattr(sa.Starlette, "_kintsugi_wrapped", False):
        return
    original = sa.Starlette.__call__      # FastAPI's __call__ ends in this one
    snapdir = os.environ.get("KINTSUGI_SNAPDIR")
    db_hook = os.environ.get("KINTSUGI_DB_PATH_HOOK")      # "module:function" -> sqlite path
    counter = {"n": 0}

    def db_path():
        if not db_hook:
            return None
        import importlib
        mod, fn = db_hook.split(":")
        try:
            return getattr(importlib.import_module(mod), fn)()
        except Exception:
            return None

    async def captured(self, scope, receive, send):
        if scope.get("type") != "http":
            return await original(self, scope, receive, send)
        if scope.get("_kintsugi_seen"):          # mounted sub-apps: capture once
            return await original(self, scope, receive, send)
        scope["_kintsugi_seen"] = True
        counter["n"] += 1
        n = counter["n"]
        body = []

        async def rec():
            m = await receive()
            if m.get("type") == "http.request":
                body.append(m.get("body", b""))
            return m
        snap = None
        if snapdir:
            src = db_path()
            if src and os.path.exists(src):
                import sqlite3
                os.makedirs(snapdir, exist_ok=True)
                snap = os.path.join(snapdir, f"snap-{os.getpid()}-{n}.db")
                a, b = sqlite3.connect(src), sqlite3.connect(snap)
                a.backup(b)
                b.close()
                a.close()
        try:
            return await original(self, scope, rec, send)
        except Exception as exc:
            frames = [{"file": f.filename, "line": f.lineno, "function": f.name, "code": f.line}
                      for f in traceback.extract_tb(exc.__traceback__)]
            headers = {k.decode("latin1"): v.decode("latin1") for k, v in scope.get("headers", [])}
            secret = ("authorization", "cookie", "x-api-key", "api-key", "x-prefect-api-key")
            event = {"exception_type": type(exc).__name__,
                     "exception_module": type(exc).__module__,
                     "exception_message": str(exc)[:500], "frames": frames,
                     "request": {"method": scope.get("method"),
                                 "path": scope.get("path", ""),
                                 "query": scope.get("query_string", b"").decode("latin1"),
                                 "headers": {k: ("[redacted]" if k.lower() in secret else v)
                                             for k, v in headers.items()},
                                 "body": b"".join(body)[:65536].decode("utf-8", "replace")},
                     "snapshot": snap}
            with open(sink, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
            raise
        finally:
            if snap and os.path.exists(snap) and not _failed_snapshot(sink, snap):
                os.remove(snap)

    sa.Starlette.__call__ = captured
    sa.Starlette._kintsugi_wrapped = True


_install()
