"""Operator sign-in for the console.

One operator account, configured by environment variables:

    KINTSUGI_ADMIN_USER       user name (default "operator")
    KINTSUGI_ADMIN_PASSWORD   password; sign-in is switched off when unset
    KINTSUGI_SESSION_SECRET   key that signs session cookies; when unset a
                              random key is made at start-up, so sessions end
                              on every restart

Public pages (results, certificates, practice, guide, privacy) need no
sign-in. Visitor accounts (console/accounts.py) may start real live runs
within a daily allowance. The live operations data (the Errors page and the
incident API) is for the operator only. Sessions are signed, time-limited cookies (HttpOnly, SameSite=Lax,
Secure over HTTPS); every form carries a signed anti-forgery token; and
repeated wrong passwords from one address are locked out for a while.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import parse_qs, quote

from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE = "kintsugi_session"
SESSION_SECONDS = 8 * 60 * 60
FORM_SECONDS = 60 * 60
MAX_FAILURES = 5
LOCKOUT_SECONDS = 10 * 60

_secret = os.environ.get("KINTSUGI_SESSION_SECRET") or secrets.token_hex(32)
_signer = URLSafeTimedSerializer(_secret, salt="kintsugi-session")
_forms = URLSafeTimedSerializer(_secret, salt="kintsugi-form")
_failures: dict[str, list[float]] = {}


def configured() -> bool:
    return bool(os.environ.get("KINTSUGI_ADMIN_PASSWORD"))


def admin_user() -> str:
    return os.environ.get("KINTSUGI_ADMIN_USER", "operator")


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def check_credentials(user: str, password: str) -> bool:
    """Constant-time comparison of both fields, so timing reveals nothing."""
    if not configured():
        return False
    user_ok = hmac.compare_digest(_digest(user), _digest(admin_user()))
    pass_ok = hmac.compare_digest(
        _digest(password), _digest(os.environ["KINTSUGI_ADMIN_PASSWORD"]))
    return user_ok and pass_ok


# ------------------------------------------------------------ sessions

def identity(request: Request) -> dict | None:
    """{"u": name or email, "r": "operator" | "user"} for a valid session."""
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        data = _signer.loads(token, max_age=SESSION_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or not data.get("u"):
        return None
    return {"u": data["u"], "r": data.get("r", "operator")}


def current_user(request: Request) -> str | None:
    who = identity(request)
    return who["u"] if who else None


def is_operator(request: Request) -> bool:
    who = identity(request)
    return bool(who) and who["r"] == "operator"


def _secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto == "https"


def start_session(response, request: Request, user: str, role: str = "operator") -> None:
    response.set_cookie(COOKIE, _signer.dumps({"u": user, "r": role,
                                               "n": secrets.token_hex(8)}),
                        max_age=SESSION_SECONDS, httponly=True, samesite="lax",
                        secure=_secure(request), path="/")


def end_session(response) -> None:
    response.delete_cookie(COOKIE, path="/")


# ---------------------------------------------------- anti-forgery tokens

def form_token(purpose: str) -> str:
    return _forms.dumps({"p": purpose, "n": secrets.token_hex(8)})


def check_form_token(token: str, purpose: str) -> bool:
    try:
        data = _forms.loads(token or "", max_age=FORM_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(data, dict) and data.get("p") == purpose


# --------------------------------------------------------- rate limiting

def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return (forwarded.split(",")[0].strip() if forwarded
            else (request.client.host if request.client else "unknown"))


def locked_for(ip: str, now: float | None = None) -> int:
    """Seconds until this address may try again; 0 when it may now."""
    now = now or time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < LOCKOUT_SECONDS]
    _failures[ip] = recent
    if len(recent) < MAX_FAILURES:
        return 0
    return int(LOCKOUT_SECONDS - (now - recent[0])) + 1


def record_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())


def clear_failures(ip: str) -> None:
    _failures.pop(ip, None)


# ----------------------------------------------------------- helpers

async def read_form(request: Request) -> dict[str, str]:
    """URL-encoded form bodies, parsed without an extra dependency."""
    body = (await request.body())[:8192].decode("utf-8", "replace")
    return {k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()}


def safe_next(value: str | None) -> str:
    """Only same-site paths: never an absolute or protocol-relative URL."""
    if value and value.startswith("/") and not value.startswith("//") \
            and "\\" not in value:
        return value
    return "/results"


def login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target)}", status_code=303)
