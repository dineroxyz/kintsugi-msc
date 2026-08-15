"""Kintsugi capture agent.

The only Kintsugi component that lives inside a managed service. It records,
per request: the request itself, the release identifier serving it, and the
request/response pair of every outbound HTTP call made while handling it.
On an unhandled exception the buffered context is flushed to the control
plane as a structured event. Everything else in the platform lives in the
control plane; a managed service is ordinary application code plus this.

Design notes (chapter three, section 3.5.1):
- outbound responses are recorded at the moment they occur, which is what
  makes hermetic replay possible later;
- redaction happens here, before anything leaves the process;
- delivery is fire-and-forget with a short timeout, so a control plane
  outage can never take a managed service down with it.
"""

from __future__ import annotations

import contextvars
import os
import re
import threading
import traceback
from typing import Any

import httpx

# Per-request capture context. A contextvar, not a global, so concurrent
# requests in the same worker do not bleed into each other's captures.
_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("kintsugi_ctx", default=None)

_REDACT_KEY = re.compile(
    r"pass(word)?|secret|token|api[-_]?key|authorization|cookie|session|credit|card|cvv|ssn",
    re.I,
)
_REDACTED = "[redacted]"
MAX_BODY = 64 * 1024  # bytes of request/response body retained


def _redact(value: Any) -> Any:
    """Recursively redact secret-looking keys in mappings."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _REDACT_KEY.search(str(k)) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _redact_headers(headers: dict) -> dict:
    return {k: (_REDACTED if _REDACT_KEY.search(k) else v) for k, v in headers.items()}


class CaptureClient(httpx.Client):
    """Drop-in httpx.Client that records outbound calls into the active capture.

    Managed services make their outbound calls through this client. Each
    request/response pair is appended to the current request's capture
    context, body-capped and header-redacted.
    """

    def send(self, request: httpx.Request, **kwargs) -> httpx.Response:  # type: ignore[override]
        response = super().send(request, **kwargs)
        ctx = _ctx.get()
        if ctx is not None:
            ctx["external_calls"].append({
                "method": request.method,
                "url": str(request.url),
                "request_headers": _redact_headers(dict(request.headers)),
                "request_body": request.content[:MAX_BODY].decode("utf-8", "replace"),
                "status": response.status_code,
                "response_headers": _redact_headers(dict(response.headers)),
                "response_body": response.content[:MAX_BODY].decode("utf-8", "replace"),
            })
        return response


class CaptureMiddleware:
    """ASGI middleware: buffer request context, flush on unhandled exception."""

    def __init__(self, app, service: str, control_plane_url: str | None = None,
                 release: str | None = None, deliver=None):
        self.app = app
        self.service = service
        self.control_plane_url = control_plane_url or os.environ.get(
            "KINTSUGI_CONTROL_PLANE", "http://localhost:8100")
        self.release = release or os.environ.get("KINTSUGI_RELEASE", "unknown")
        # injectable delivery so tests can hand events to an in-process app
        # synchronously; production uses the default fire-and-forget post
        self._deliver_override = deliver

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        body_chunks: list[bytes] = []

        async def buffering_receive():
            message = await receive()
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
            return message

        token = _ctx.set({
            "external_calls": [],
        })
        try:
            await self.app(scope, buffering_receive, send)
        except Exception as exc:
            ctx = _ctx.get() or {"external_calls": []}
            event = self._build_event(scope, b"".join(body_chunks), exc, ctx)
            self._deliver(event)
            raise
        finally:
            _ctx.reset(token)

    def _build_event(self, scope, body: bytes, exc: Exception, ctx: dict) -> dict:
        headers = {k.decode("latin1"): v.decode("latin1") for k, v in scope.get("headers", [])}
        tb = traceback.extract_tb(exc.__traceback__)
        frames = [
            {"file": f.filename, "line": f.lineno, "function": f.name, "code": f.line}
            for f in tb
        ]
        return {
            "service": self.service,
            "release": self.release,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "frames": frames,
            "request": {
                "method": scope.get("method"),
                "path": scope.get("path"),
                "query": scope.get("query_string", b"").decode("latin1"),
                "headers": _redact_headers(headers),
                "body": body[:MAX_BODY].decode("utf-8", "replace"),
            },
            "external_calls": _redact(ctx["external_calls"]),
        }

    def _deliver(self, event: dict) -> None:
        """Fire-and-forget delivery; a control plane outage must never
        cascade into the managed service."""
        if self._deliver_override is not None:
            self._deliver_override(event)
            return

        def post():
            try:
                with httpx.Client(timeout=3.0) as client:
                    client.post(f"{self.control_plane_url}/ingest/event", json=event)
            except Exception:
                pass  # deliberately swallowed; see docstring

        threading.Thread(target=post, daemon=True).start()
