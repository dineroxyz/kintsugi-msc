"""pytest plugin: attach Kintsugi's capture to ANY Starlette/FastAPI app.

Every application's entry point (Starlette.__call__, which FastAPI's ends in) is wrapped, so
an unhandled exception that leaves the application is recorded to the file
named by KINTSUGI_SINK, whatever code created the app. Standard library only,
so it runs inside any project's environment.
"""
import json
import os
import traceback


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

    async def captured(self, scope, receive, send):
        if scope.get("type") != "http":
            return await original(self, scope, receive, send)
        try:
            return await original(self, scope, receive, send)
        except Exception as exc:
            frames = [{"file": f.filename, "line": f.lineno, "function": f.name, "code": f.line}
                      for f in traceback.extract_tb(exc.__traceback__)]
            event = {"exception_type": type(exc).__name__,
                     "exception_module": type(exc).__module__,
                     "exception_message": str(exc)[:500], "frames": frames,
                     "request": {"method": scope.get("method"), "path": scope.get("path"),
                                 "query": scope.get("query_string", b"").decode("latin1")}}
            with open(sink, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
            raise

    sa.Starlette.__call__ = captured
    sa.Starlette._kintsugi_wrapped = True


_install()
