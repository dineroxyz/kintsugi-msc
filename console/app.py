"""Kintsugi operations console.

Server-rendered, no build step. Routes only: data comes from console.data,
markup from console.views, styling and enhancement from console/static.
Every page works without JavaScript; the script only makes the explorer,
grid and dialogs faster. Mounts the control plane API under /api.

    uvicorn console.app:app --port 8100
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles

from console import accounts as A          # noqa: F401  (registers its tables)
from console import heal as H
from console import auth
from console import data as D
from console import live as L
from console import pages as P
from console import views as V
from control_plane.api.main import app as control_plane_api
from control_plane.certificates.issuer import (CertificateError,
                                               CertificateStore)
from control_plane.practice.mode import PracticeBank

E = V.E
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
STUDY_DIR = os.environ.get("KINTSUGI_STUDY_DIR",
                           os.path.join(ROOT, "study", "pilot"))
CERT_DIR = os.environ.get("KINTSUGI_CERT_DIR",
                          os.path.join(STUDY_DIR, "certificates"))
BANK_PATH = os.environ.get("KINTSUGI_BANK_PATH",
                           os.path.join(ROOT, "study", "practice_bank.jsonl"))
VERIFY_PATH = os.path.join(os.path.dirname(CERT_DIR),
                           "certificate_verification.json")
SUPP_DIR = os.environ.get("KINTSUGI_SUPP_DIR",
                          os.path.join(os.path.dirname(STUDY_DIR), "supp"))
CORRECTNESS_PATH = os.path.join(os.path.dirname(STUDY_DIR), "correctness_summary.json")
HAIKU_DIR = os.environ.get("KINTSUGI_HAIKU_DIR",
                           os.path.join(os.path.dirname(STUDY_DIR), "haiku"))
_request: ContextVar[Request | None] = ContextVar("request", default=None)
PAGE_SIZE = 50


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # a mounted sub-app's own lifespan never runs under Starlette mounts,
    # so the control plane's table creation must happen here
    try:
        from control_plane.db import init_db
        init_db()
    except Exception as exc:      # console must render even without a DB
        print(f"console: init_db failed ({exc}); API ingest will be down")
    yield


app = FastAPI(title="Kintsugi Console", lifespan=_lifespan)
app.mount("/api", control_plane_api)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")),
          name="static")


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"),
}


@app.middleware("http")
async def _context(request: Request, call_next):
    token = _request.set(request)
    try:
        # the live incident data is for the signed-in operator only;
        # ingest (POST) stays open so the services can report failures
        if (request.method == "GET" and request.url.path.startswith("/api/incidents")
                and not is_admin(request)):
            return JSONResponse({"error": "sign in required"}, status_code=401)
        response = await call_next(request)
    finally:
        _request.reset(token)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


def is_admin(request: Request) -> bool:
    """The operator login, or a signed-in owner account."""
    who = auth.identity(request)
    if not who:
        return False
    return who["r"] == "operator" or A.is_admin(who["u"])


def _unseen_alerts(who: dict | None) -> int:
    if not who or who["r"] != "user":
        return 0
    try:
        user = A.get_user(who["u"])
        return H.unseen_count([s.id for s in A.user_services(user.id)]) if user else 0
    except Exception:
        return 0


def page(title: str, active: str, body: str, status: int = 200,
         wide: bool = False, extra_head: str = "") -> HTMLResponse:
    request = _request.get()
    who = auth.identity(request) if request else None
    user = who["u"] if who else None
    html = V.shell(title, active, body, user=user, wide=wide,
                   csrf=auth.form_token("logout") if user else "",
                   extra_head=extra_head, role=who["r"] if who else None,
                   admin=bool(who) and who["r"] == "user" and A.is_admin(who["u"]),
                   alerts=_unseen_alerts(who))
    return HTMLResponse(html, status_code=status,
                        headers={"Cache-Control": "no-store"} if user else None)


def as_page_heading(fragment: str) -> str:
    """Detail fragments open with an h2 (they live inside a dialog); as a
    page of their own, that heading is the page's h1."""
    html = fragment.replace("<h2>", "<h1>", 1).replace("</h2>", "</h1>", 1)
    return html.replace("<h3", "<h2").replace("</h3>", "</h2>")


def not_found(what: str, back: str, back_label: str) -> HTMLResponse:
    return page("Not found", "", f"<h1>{E(what)}</h1><p class=lede>It may "
                f'have been mistyped. <a href="{back}">{E(back_label)}</a>.</p>',
                status=404)


# ------------------------------------------------------------ data access

def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


@lru_cache(maxsize=4)
def _trials(path: str, mtime: float) -> list[dict]:
    return D.load_trials(os.path.dirname(path))


def trials() -> list[dict]:
    path = os.path.join(STUDY_DIR, "trials.jsonl")
    return _trials(path, _mtime(path))


@lru_cache(maxsize=1)
def titles() -> dict[str, str]:
    try:
        from corpus import load_corpus
        return {e.id: e.title for e in load_corpus()}
    except Exception:
        return {}


@lru_cache(maxsize=1)
def origins() -> dict[str, int]:
    """How many corpus bugs were mined from real fixes and how many seeded."""
    try:
        from corpus import load_corpus
        counts: dict[str, int] = {}
        for e in load_corpus():
            o = (e.provenance or {}).get("origin", "unknown")
            counts[o] = counts.get(o, 0) + 1
        return counts
    except Exception:
        return {}


@lru_cache(maxsize=4)
def _cert_seq(cert_dir: str, mtime: float) -> dict[str, int]:
    try:
        return {c["hash"]: c["sequence"] for c in CertificateStore(cert_dir).all()}
    except Exception:
        return {}


def cert_seq() -> dict[str, int]:
    return _cert_seq(CERT_DIR, _mtime(CERT_DIR))


def verification() -> dict:
    try:
        with open(VERIFY_PATH) as fh:
            data = json.load(fh)
    except Exception:
        return {}
    data["by_sequence"] = {r["sequence"]: r
                           for r in data.get("replay", {}).get("rows", [])}
    return data


# ---------------------------------------------------------------- results

HERO_REPLAY_ENTRY = os.environ.get("KINTSUGI_HERO_REPLAY", "M05")


@app.get("/", response_class=HTMLResponse)
def home():
    recs = [r for r in L.recordings()
            if r["entry"] == HERO_REPLAY_ENTRY and r.get("outcome") == "recovered"]
    rec = recs[0] if recs else next(
        (r for r in L.recordings() if r.get("outcome") == "recovered"), None)
    return page("Production software that mends itself", "home",
                P.home(trials(), len(cert_seq()), SUPP_DIR,
                       hero_replay=rec["name"] if rec else None, hero_meta=rec,
                       weak=P.weaker_model(HAIKU_DIR)),
                wide=True,
                extra_head='<link rel="stylesheet" href="/static/home.css">\n'
                           '<script src="/static/home.js" defer></script>')


@app.get("/guide", response_class=HTMLResponse)
def guide():
    return page("How to use this site", "guide", P.guide())


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return page("Privacy policy", "privacy", P.privacy())


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/live"):
    if auth.current_user(request):
        return RedirectResponse(auth.safe_next(next), status_code=303)
    return page("Sign in", "login",
                P.login(auth.form_token("login"), auth.safe_next(next),
                        locked=auth.locked_for(auth.client_ip(request))))


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await auth.read_form(request)
    nxt = auth.safe_next(form.get("next"))
    ip = auth.client_ip(request)

    def again(error: str, status: int):
        return page("Sign in", "login",
                    P.login(auth.form_token("login"), nxt, error=error,
                            user=form.get("user", "")[:80],
                            locked=auth.locked_for(ip)), status=status)

    if not auth.check_form_token(form.get("csrf", ""), "login"):
        return again("This form expired. Please try again.", 400)
    if auth.locked_for(ip):
        return again("", 429)
    name, password = form.get("user", "").strip(), form.get("password", "")
    if auth.configured() and name == auth.admin_user():
        role = "operator" if auth.check_credentials(name, password) else None
    else:
        role = "user" if A.authenticate(name, password) else None
    if role is None:
        auth.record_failure(ip)
        return again("That email and password do not match. Check them and try "
                     "again.", 401)
    auth.clear_failures(ip)
    response = RedirectResponse(nxt, status_code=303)
    auth.start_session(response, request,
                       auth.admin_user() if role == "operator" else A.normalise(name), role)
    return response


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    if auth.current_user(request):
        return RedirectResponse("/account", status_code=303)
    return page("Create an account", "signup",
                P.signup(auth.form_token("signup"), runs=A.RUNS_PER_ACCOUNT_PER_DAY))


@app.post("/signup", response_class=HTMLResponse)
async def signup_submit(request: Request):
    form = await auth.read_form(request)
    ip = auth.client_ip(request)
    email = form.get("email", "")[:254]

    def again(error: str, status: int):
        return page("Create an account", "signup",
                    P.signup(auth.form_token("signup"), runs=A.RUNS_PER_ACCOUNT_PER_DAY,
                             error=error, email=email), status=status)

    if not auth.check_form_token(form.get("csrf", ""), "signup"):
        return again("This form expired. Please try again.", 400)
    if form.get("agree") != "yes":
        return again("Please confirm you have read the privacy policy.", 400)
    if not A.signup_allowed(ip):
        return again("Too many new accounts from this network. Try again in an hour.", 429)
    try:
        A.create_user(email, form.get("password", ""))
    except ValueError as exc:
        return again(str(exc), 400)
    A.record_signup(ip)
    response = RedirectResponse("/live?welcome=1", status_code=303)
    auth.start_session(response, request, A.normalise(email), "user")
    return response


@app.get("/account", response_class=HTMLResponse)
def account(request: Request):
    who = auth.identity(request)
    if not who:
        return auth.login_redirect(request)
    if who["r"] == "operator":
        return RedirectResponse("/admin", status_code=303)
    user = A.get_user(who["u"])
    if user is None:                                   # deleted elsewhere
        response = RedirectResponse("/", status_code=303)
        auth.end_session(response)
        return response
    return _account_page(request, user)


def _account_page(request: Request, user, *, new_key: tuple | None = None,
                  service_error: str = "", status: int = 200,
                  password_msg: tuple | None = None):
    user_services = A.user_services(user.id)
    services = [(svc, A.service_incidents(svc), H.repo_of(svc.id)) for svc in user_services]
    alerts = H.jobs_for([svc.id for svc in user_services])
    base = os.environ.get("KINTSUGI_PUBLIC_URL", str(request.base_url).rstrip("/"))
    return page("Your account", "account",
                P.account(user, A.user_runs(user.id), {**A.runs_left(user.id), "unlimited": A.is_unlimited(user.email)},
                          titles(), auth.form_token("delete"), live_enabled=L.enabled(),
                          services=services, service_token=auth.form_token("service"),
                          new_key=new_key, service_error=service_error, base_url=base,
                          password_token=auth.form_token("password"), password_msg=password_msg,
                          admin=A.is_admin(user.email), alerts=alerts),
                status=status)


@app.post("/account/password")
async def account_password(request: Request):
    who = auth.identity(request)
    form = await auth.read_form(request)
    if not who or who["r"] != "user" or not auth.check_form_token(form.get("csrf", ""), "password"):
        return RedirectResponse("/account", status_code=303)
    user = A.get_user(who["u"])
    if user is None:
        return RedirectResponse("/", status_code=303)
    try:
        A.change_password(user.id, form.get("current", ""), form.get("new", ""))
    except ValueError as exc:
        return _account_page(request, user, password_msg=(False, str(exc)), status=400)
    return _account_page(request, user, password_msg=(True, "Password changed."))


@app.post("/account/services")
async def add_service(request: Request):
    who = auth.identity(request)
    form = await auth.read_form(request)
    if not who or who["r"] != "user" or not auth.check_form_token(form.get("csrf", ""), "service"):
        return RedirectResponse("/account", status_code=303)
    user = A.get_user(who["u"])
    if user is None:
        return RedirectResponse("/", status_code=303)
    try:
        svc, key = A.create_service(user.id, form.get("name", ""))
    except ValueError as exc:
        return _account_page(request, user, service_error=str(exc), status=400)
    return _account_page(request, user, new_key=(svc, key))


@app.post("/account/services/{service_id}/delete")
async def remove_service(service_id: int, request: Request):
    who = auth.identity(request)
    form = await auth.read_form(request)
    if who and who["r"] == "user" and auth.check_form_token(form.get("csrf", ""), "service"):
        user = A.get_user(who["u"])
        if user:
            A.delete_service(user.id, service_id)
    return RedirectResponse("/account#services", status_code=303)


REQUIRED_EVENT = ("exception_type", "frames", "request")


@app.post("/connect/{key}/ingest/event")
async def connected_ingest(key: str, request: Request):
    """Failures from a visitor's own service. The key decides which account
    the incident belongs to; the event is checked before it is stored."""
    svc = A.service_by_key(key)
    if svc is None:
        return JSONResponse({"error": "unknown key"}, status_code=401)
    if not A.ingest_allowed(svc.id):
        return JSONResponse({"error": "too many events; slow down"}, status_code=429)
    body = await request.body()
    if len(body) > 256 * 1024:
        return JSONResponse({"error": "event too large"}, status_code=413)
    try:
        event = json.loads(body)
        assert isinstance(event, dict)
        assert all(k in event for k in REQUIRED_EVENT)
        assert isinstance(event["frames"], list) and isinstance(event["request"], dict)
    except Exception:
        return JSONResponse({"error": "not a Kintsugi capture event"}, status_code=400)
    event["service"] = A.service_tag(svc)
    event.setdefault("release", "unknown")
    event.setdefault("exception_message", "")
    event.setdefault("external_calls", [])
    from control_plane.db import get_session
    from control_plane.incidents.former import ingest
    with get_session() as session:
        incident, capture = ingest(session, event)
        result = {"incident_id": incident.id, "capture_count": incident.capture_count}
    try:
        H.open_job(svc.id, result["incident_id"])
    except Exception as exc:                      # alerting must never block ingest
        print(f"heal: could not open job ({exc})")
    return result


@app.post("/account/delete")
async def account_delete(request: Request):
    who = auth.identity(request)
    form = await auth.read_form(request)
    if not who or who["r"] != "user" or not auth.check_form_token(form.get("csrf", ""), "delete"):
        return RedirectResponse("/account", status_code=303)
    if form.get("confirm", "").strip().lower() != who["u"]:
        return RedirectResponse("/account?confirm=1", status_code=303)
    user = A.get_user(who["u"])
    if user:
        A.delete_user(user.id)
    response = RedirectResponse("/?deleted=1", status_code=303)
    auth.end_session(response)
    return response


@app.post("/logout")
async def logout(request: Request):
    form = await auth.read_form(request)
    response = RedirectResponse("/", status_code=303)
    if auth.check_form_token(form.get("csrf", ""), "logout"):
        auth.end_session(response)
    return response


# ------------------------------------------------------------------- live

def live_status(request: Request) -> dict:
    """Whether this visitor may start a real run now, and why not if not.
    The operator is unlimited; an account has a daily allowance; the site
    has a daily ceiling; without live runs enabled, only replays exist."""
    if not L.enabled():
        return {"can": False, "why": "replay"}
    who = auth.identity(request)
    if who and who["r"] == "operator":
        return {"can": True, "why": "operator", "user_id": None}
    if not auth.configured() and not who:
        return {"can": True, "why": "local", "user_id": None}   # laptop, no accounts
    if not who:
        return {"can": False, "why": "signin"}
    user = A.get_user(who["u"])
    if user is None:
        return {"can": False, "why": "signin"}
    if A.is_unlimited(user.email):
        return {"can": True, "why": "owner", "user_id": user.id}
    left = A.runs_left(user.id)
    if left["site"] <= 0:
        return {"can": False, "why": "site_cap", **left}
    if left["account"] <= 0:
        return {"can": False, "why": "account_cap", **left}
    return {"can": True, "why": "account", "user_id": user.id, **left}


@app.get("/live", response_class=HTMLResponse)
def live_page(request: Request):
    from corpus import load_corpus
    try:
        entries = load_corpus()
    except Exception:
        entries = []
    body = P.live(entries, L.recordings(), live_enabled=L.enabled(),
                  repairs=L.repair_count(CERT_DIR), csrf=auth.form_token("live"),
                  status=live_status(request),
                  welcome=request.query_params.get("welcome") == "1",
                  recent=A.recent_runs(72, 8), titles=titles())
    return page("Break it, and watch it mend", "live", body, wide=True,
                extra_head='<link rel="stylesheet" href="/static/live.css">\n'
                           '<script src="/static/live.js" defer></script>')


@app.post("/live/run")
async def live_run(request: Request):
    form = await auth.read_form(request)
    if not auth.check_form_token(form.get("csrf", ""), "live"):
        return JSONResponse({"error": "This page expired. Reload it and try again."},
                            status_code=400)
    st = live_status(request)
    if not st["can"]:
        why = {"replay": "Live runs are not available here.",
               "signin": "Create a free account or sign in to run it for real.",
               "account_cap": "You have used today's runs. They refresh 24 hours after each run.",
               "site_cap": "Today's runs for the whole site are used up. Try again tomorrow."}
        return JSONResponse({"error": why.get(st["why"], "Not available.")}, status_code=403)
    if L.busy():
        return JSONResponse({"error": "Someone else's run is in progress. Try again in a minute."},
                            status_code=409)
    entry = form.get("entry", "")
    try:
        from corpus import load_entry
        load_entry(entry)
    except Exception:
        return JSONResponse({"error": "Pick a bug from the list."}, status_code=400)
    record = A.start_run(st.get("user_id"), entry)
    try:
        run = L.start(entry, on_finish=lambda **kw: A.finish_run(record, **kw))
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)
    who = auth.identity(request)
    _run_owner[run.id] = who["u"] if who else None
    return {"run": run.id}


_run_owner: dict[str, str | None] = {}


@app.post("/live/{run_id}/{action}")
async def live_undo_redo(run_id: str, action: str, request: Request):
    """Undo a live repair (the bug comes back) or redo it from its
    certificate (no model call). Only whoever ran it, while the shop is kept."""
    form = await auth.read_form(request)
    if action not in ("undo", "redo"):
        return JSONResponse({"error": "unknown action"}, status_code=404)
    if not auth.check_form_token(form.get("csrf", ""), "live"):
        return JSONResponse({"error": "This page expired. Reload it and try again."}, status_code=400)
    who = auth.identity(request)
    if not who or _run_owner.get(run_id) != who["u"]:
        return JSONResponse({"error": "Only the person who ran this repair can undo or redo it."},
                            status_code=403)
    try:
        run = L.act(run_id, action)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return {"run": run.id}


def _sse(events):
    for e in events:
        yield f"data: {json.dumps(e)}\n\n"


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@app.get("/live/stream/{run_id}")
def live_stream(run_id: str):
    run = L.get(run_id)
    if run is None:
        return JSONResponse({"error": "no such run"}, status_code=404)

    def gen():
        i = 0
        while True:
            fresh = run.wait_from(i)
            i += len(fresh)
            yield from _sse(fresh)
            if not fresh:
                yield ": keep-alive\n\n"
            if run.done and i >= len(run.events):
                return
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers=SSE_HEADERS)


@app.get("/live/replay/{name}")
def live_replay(name: str):
    rec = L.load_recording(name)
    if rec is None:
        return JSONResponse({"error": "no such recording"}, status_code=404)
    meta, events = rec

    def gen():
        import time as _t
        yield from _sse([{**meta, "t": 0, "kind": "replay"}])
        start = _t.time()
        for e in events:
            delay = e.get("t", 0) - (_t.time() - start)
            if delay > 0:
                _t.sleep(delay)
            yield from _sse([e])
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers=SSE_HEADERS)


@app.get("/results", response_class=HTMLResponse)
def results(request: Request):
    ts = trials()
    if not ts:
        return page("Results", "results",
                    "<h1>No results yet</h1><p class=lede>Once the tests have "
                    "been run, their results will show up here.</p>")
    check = D.integrity(ts)
    q = D.Query.parse(dict(request.query_params))
    t = titles()
    body = (
        V.hero(ts, check, origins())
        + '<div class="divider">' + V.seam() + "</div>"
        + '<section class="section" aria-labelledby="grid-title">'
        '<div class="section-head"><h2 id="grid-title">Every bug, every approach</h2>'
        "<p>Bugs that can be fixed are on the left. On the right are bugs that "
        "only happen under conditions that cannot be recreated on demand, such "
        "as two requests colliding. The safe choice for those is to leave them "
        "for a person.</p></div>"
        '<div class="viz"><div class="viz-tools">'
        + V.legend(V.MAIN_OUTCOMES) + "</div>"
        + V.matrix(ts, t) + V.matrix_table(ts, t) + "</div></section>"
        + '<section class="section" aria-labelledby="compare-title">'
        '<div class="section-head"><h2 id="compare-title">How each approach did</h2>'
        "<p>The same tests, counted. Restarting and rolling back fixed every "
        "bug fastest, because every bug here arrived with a new version and the "
        "previous version was always there to go back to. The AI approaches "
        "matter when it is not.</p></div>"
        + V.compare_table(ts) + "</section>"
        + V.weaker_model_section(P.weaker_model(HAIKU_DIR))
        + V.correctness_section(P.correctness(CORRECTNESS_PATH))
        + V.ablation(ts)
        + V.explorer(ts, q, t))
    return page("Results", "results", body)


@app.get("/fragment/explorer", response_class=HTMLResponse)
def explorer_fragment(request: Request):
    q = D.Query.parse(dict(request.query_params))
    return HTMLResponse(V.explorer_results(trials(), q, titles()))


@app.get("/bugs/{bug}", response_class=HTMLResponse)
def bug_page(bug: str):
    html = V.bug_detail(trials(), bug, titles())
    if html is None:
        return not_found(f"No bug called {bug}", "/results", "Back to the results")
    return page(f"Bug {bug}", "results", as_page_heading(html))


@app.get("/fragment/bug/{bug}", response_class=HTMLResponse)
def bug_fragment(bug: str):
    html = V.bug_detail(trials(), bug, titles())
    if html is None:
        return HTMLResponse("<p>No such bug.</p>", status_code=404)
    return HTMLResponse(html)


def _trial(test_id: int) -> dict | None:
    ts = trials()
    return ts[test_id] if 0 <= test_id < len(ts) else None


@app.get("/tests/{test_id}", response_class=HTMLResponse)
def test_page(test_id: int):
    t = _trial(test_id)
    if t is None:
        return not_found(f"No test number {test_id}", "/results#tests", "See every test")
    return page(f"Test {test_id}", "results",
                as_page_heading(V.test_detail(t, titles(), cert_seq()))
                + f'<p class="links"><a href="/bugs/{E(t["entry"])}">Every test '
                f'of bug {E(t["entry"])}</a><a href="/results#tests">All tests</a></p>')


@app.get("/fragment/test/{test_id}", response_class=HTMLResponse)
def test_fragment(test_id: int):
    t = _trial(test_id)
    if t is None:
        return HTMLResponse("<p>No such test.</p>", status_code=404)
    return HTMLResponse(V.test_detail(t, titles(), cert_seq()))


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(os.path.join(HERE, "static", "favicon.svg"),
                        media_type="image/svg+xml")


@app.get("/data/trials.json")
def trials_json():
    if not os.path.exists(os.path.join(STUDY_DIR, "trials.jsonl")):
        return JSONResponse({"error": "no trial log"}, status_code=404)
    return JSONResponse(trials())


# ------------------------------------------------------------------ trust

ATTESTATION = os.path.join(os.path.dirname(CERT_DIR), "attestation.json")


def _published_key() -> dict:
    from control_plane.certificates import pubsig
    pub = pubsig.public_key_b64()
    if pub is None and os.path.exists(ATTESTATION):       # key from the attestation
        try:
            pub = json.load(open(ATTESTATION))["key"]
        except Exception:
            pub = None
    return {"alg": "Ed25519", "key": pub, "key_id": pubsig.key_id(pub) if pub else None}


@app.get("/.well-known/kintsugi-key.json")
def well_known_key():
    return JSONResponse(_published_key())


@app.get("/data/attestation.json")
def attestation_json():
    if not os.path.exists(ATTESTATION):
        return JSONResponse({"error": "no attestation"}, status_code=404)
    return FileResponse(ATTESTATION, media_type="application/json")


@app.get("/data/attestation.json.ots")
def attestation_ots():
    path = ATTESTATION + ".ots"
    if not os.path.exists(path):
        return JSONResponse({"error": "not anchored yet"}, status_code=404)
    return FileResponse(path, media_type="application/octet-stream",
                        filename="attestation.json.ots")


def chains() -> dict[str, dict]:
    """The certificate chains this site publishes."""
    return {
        "study": {"dir": CERT_DIR, "base": "/certificates", "title": "Study repairs",
                  "intro": "The repairs made during the pre-registered study."},
        "live": {"dir": L.LIVE_CERTS, "base": "/live-certificates", "title": "Live repairs",
                 "intro": "Repairs made on the Live page, by visitors pressing Break it."},
    }


def _chain_certs(chain: str) -> dict[int, dict]:
    d = chains()[chain]["dir"]
    if not os.path.isdir(d):
        return {}
    return {c["sequence"]: c for c in CertificateStore(d).all()}


def _canonical(chain: str, seq: int):
    from fastapi.responses import Response
    from control_plane.certificates.issuer import canonical_bytes
    cert = _chain_certs(chain).get(seq)
    if cert is None:
        return JSONResponse({"error": "no such certificate"}, status_code=404)
    return Response(canonical_bytes(cert), media_type="application/octet-stream",
                    headers={"X-Certificate-Hash": cert["hash"]})


def _cert_meta(chain: str, seq: int):
    cert = _chain_certs(chain).get(seq)
    if cert is None:
        return JSONResponse({"error": "no such certificate"}, status_code=404)
    return JSONResponse({"sequence": seq, "hash": cert["hash"], "chain": chain,
                         "prev_hash": cert["prev_hash"], "ed25519": cert.get("ed25519")})


@app.get("/certificates/{seq}/canonical")
def certificate_canonical(seq: int):
    """Exactly the bytes the certificate's hash covers, so a browser can
    recompute the hash itself instead of trusting this server's word."""
    return _canonical("study", seq)


@app.get("/certificates/{seq}.json")
def certificate_json(seq: int):
    return _cert_meta("study", seq)


@app.get("/live-certificates/{seq}/canonical")
def live_certificate_canonical(seq: int):
    return _canonical("live", seq)


@app.get("/live-certificates/{seq}.json")
def live_certificate_json(seq: int):
    return _cert_meta("live", seq)


# ----------------------------------------------------------------- status

_status_cache: dict = {"at": 0.0, "checks": None}


def _status_checks() -> list[dict]:
    """Ask each watched service whether it answers, now (cached 20 s so the
    page cannot be used to hammer them). No background job: the page checks
    when it is opened."""
    import time as _t
    import httpx
    if _status_cache["checks"] is not None and _t.time() - _status_cache["at"] < 20:
        return _status_cache["checks"]
    out = []
    for pair in os.environ.get("KINTSUGI_STATUS_TARGETS", "").split(","):
        if "=" not in pair:
            continue
        name, url = (x.strip() for x in pair.split("=", 1))
        t0 = _t.time()
        try:
            ok = httpx.get(url, timeout=5.0).status_code < 500
        except httpx.HTTPError:
            ok = False
        out.append({"name": name, "ok": ok, "ms": round((_t.time() - t0) * 1000)})
    _status_cache.update(at=_t.time(), checks=out)
    return out


@app.get("/status", response_class=HTMLResponse)
def status():
    return page("Status", "status",
                P.status(_status_checks(), A.recent_runs(24), titles()))


@app.get("/built", response_class=HTMLResponse)
def built():
    return page("How it is built", "built",
                P.built(ROOT, len(cert_seq()), len(trials())), wide=False)


@app.get("/trust", response_class=HTMLResponse)
def trust():
    att = None
    if os.path.exists(ATTESTATION):
        try:
            att = json.load(open(ATTESTATION))
        except Exception:
            att = None
    return page("Why you can trust the record", "trust",
                P.trust(_published_key(), att, os.path.exists(ATTESTATION + ".ots"),
                        len(cert_seq())))


@app.get("/verification", response_class=HTMLResponse)
def verification_page():
    v = verification()
    v.pop("by_sequence", None)
    return page("Certificate check report", "certificates", P.verification_report(v, cert_seq()))


@app.get("/data/correctness.json")
def correctness_json():
    if not os.path.exists(CORRECTNESS_PATH):
        return JSONResponse({"error": "no correctness check yet"}, status_code=404)
    return FileResponse(CORRECTNESS_PATH, media_type="application/json")


@app.get("/data/verification.json")
def verification_json():
    if not os.path.exists(VERIFY_PATH):
        return JSONResponse({"error": "no verification report yet"},
                            status_code=404)
    return FileResponse(VERIFY_PATH, media_type="application/json")


# ----------------------------------------------------------- certificates

@app.get("/certificates", response_class=HTMLResponse)
def certificates(page_no: int = 1):
    return _certificate_list("study", page_no)


@app.get("/live-certificates", response_class=HTMLResponse)
def live_certificates(page_no: int = 1):
    return _certificate_list("live", page_no)


def _chain_tabs(active: str) -> str:
    return ('<nav class="chain-tabs" aria-label="Certificate chains">' + "".join(
        f'<a href="{c["base"]}"{" aria-current=page" if k == active else ""}>{E(c["title"])}</a>'
        for k, c in chains().items()) + "</nav>")


def _certificate_list(chain: str, page_no: int):
    info_key = chain
    info = chains()[chain]
    base = info["base"]
    if chain == "live":
        os.makedirs(info["dir"], exist_ok=True)
    store = CertificateStore(info["dir"])
    try:
        chain = store.verify_chain()
        banner = (f'<div class="banner">{V.seal("chain", 28)}<p style="margin:0">'
                  f"<b>All {len(chain)} certificates check out.</b> "
                  '<span class="muted">None has been changed, none is missing, '
                  "and every one is signed by Kintsugi.</span></p></div>")
    except CertificateError as exc:
        chain = store.all()
        banner = (f'<div class="banner" role="alert"><span class="sw k-broken" '
                  f'aria-hidden="true"></span><p style="margin:0"><b>Something is '
                  f"wrong with a certificate.</b> {E(str(exc))}</p></div>")
    if not chain:
        return page("Certificates", "certificates",
                    "<h1>Repair certificates</h1>" + _chain_tabs(info_key) +
                    "<p class=lede>No certificates in this chain yet. Each time "
                    "Kintsugi puts a fix live, it writes a certificate, and it will "
                    'show up here. <a href="/live">Break a bug</a>.</p>')

    issued_by = {h: t for t in trials()
                 for h in (t.get("detail") or {}).get("certificates", [])}
    if info_key == "live":
        issued_by = {}
    t_names = titles()
    pages = max(1, -(-len(chain) // PAGE_SIZE))
    page_no = min(max(1, page_no), pages)
    shown = list(reversed(chain))[(page_no - 1) * PAGE_SIZE: page_no * PAGE_SIZE]
    items = []
    for c in shown:
        t = issued_by.get(c["hash"], {})
        ctx = []
        if t.get("entry"):
            ctx.append(f"Bug {t['entry']}: {t_names.get(t['entry'], '')}".rstrip(": "))
        if t.get("variant"):
            ctx.append(D.VARIANT_NAME.get(t["variant"], t["variant"]))
        items.append(
            f'<li>{V.seal(c["hash"])}<div><a href="{base}/{c["sequence"]}">'
            f'Certificate {c["sequence"]:06d}</a>'
            f'<div class="meta">{E(". ".join(ctx))}</div>'
            f'<div class="meta">Written {E(c["issued_at"].replace("T", " at "))}, '
            f'ID <span class="mono">{c["hash"][:16]}</span></div></div></li>')
    nav = ['<nav class="pager" aria-label="Pages">']
    nav.append(f'<a class="btn" href="{base}?page_no={page_no - 1}">Newer</a>'
               if page_no > 1 else '<span class="btn" aria-disabled="true">Newer</span>')
    nav.append(f'<span class="muted small">Page {page_no} of {pages}</span>')
    nav.append(f'<a class="btn" href="{base}?page_no={page_no + 1}">Older</a>'
               if page_no < pages else '<span class="btn" aria-disabled="true">Older</span>')
    nav.append("</nav>")
    body = ("<h1>Repair certificates</h1>"
            "<p class=lede>Each time Kintsugi puts a fix live, it writes a "
            "certificate: what went wrong, proof that the error could be "
            "repeated, every fix the AI tried, and which checks the final fix "
            "passed. Each certificate is linked to the one before it, like a "
            "chain, so if anyone changes or deletes one, it shows.</p>"
            + _chain_tabs(info_key) + f'<p class="muted">{E(info["intro"])}</p>'
            f'{banner}<ol class="ledger">{"".join(items)}</ol>{"".join(nav)}')
    return page("Certificates", "certificates", body)


COND_NAME = {"structural": "Not just hiding the error",
             "applies": "Fix is written correctly",
             "reproduction": "The error is gone",
             "suite": "Nothing else broke"}


@app.get("/certificates/{seq}", response_class=HTMLResponse)
def certificate(seq: int):
    return _certificate_page("study", seq)


@app.get("/live-certificates/{seq}", response_class=HTMLResponse)
def live_certificate(seq: int):
    return _certificate_page("live", seq)


def _certificate_page(chain: str, seq: int):
    base = chains()[chain]["base"]
    certs = _chain_certs(chain)
    cert = certs.get(seq)
    if cert is None:
        return not_found("No such certificate", base, "See all certificates")
    t = (next((x for x in trials()
               if cert["hash"] in (x.get("detail") or {}).get("certificates", [])), {})
         if chain == "study" else {})
    title = titles().get(t.get("entry", ""), "")
    capture = cert.get("capture") or {}
    req = capture.get("request") or {}
    verdicts = (cert.get("reproduction") or {}).get("verdicts") or []

    cands = []
    for cand in cert.get("candidates", []):
        conds = cand.get("conditions") if isinstance(cand.get("conditions"), dict) else {}
        ok = bool(conds) and all(conds.values())
        chips = "".join(f'<li class="{"pass" if v else "fail"}">{COND_NAME.get(k, k)}</li>'
                        for k, v in conds.items())
        cands.append(f'<li><b>Try {cand.get("attempt")}</b>: '
                     f'{"accepted" if ok else "turned down"}<ul class="conds">{chips}</ul></li>')

    ver = verification().get("by_sequence", {}).get(cert["sequence"]) if chain == "study" else None
    if ver:
        replay = ("<p><b>Checked again separately.</b> Using only this certificate "
                  "and a fresh copy of the broken app, "
                  + ("the error happened before the fix and stopped after it."
                     if ver.get("verified") else "the fix could not be confirmed.")
                  + "</p>")
    else:
        replay = ('<p class="muted">Anyone with this certificate can check the fix '
                  "again: the error should happen before the fix and stop after it.</p>")
    links = []
    if seq - 1 in certs:
        links.append(f'<a href="{base}/{seq - 1}">Previous certificate</a>')
    links.append(f'<a href="{base}">All certificates</a>')
    if seq + 1 in certs:
        links.append(f'<a href="{base}/{seq + 1}">Next certificate</a>')
    bug_link = (f', <a href="/bugs/{E(t["entry"])}">bug {E(t["entry"])}</a>'
                if t.get("entry") else "")

    check = (f'<div class="verify-box" data-verify="{cert["sequence"]}" data-base="{base}">'
             '<button class="btn" type="button" data-verify-go>Check it yourself</button>'
             '<span class="muted small">Your browser recomputes the fingerprint, the link '
             'to the previous certificate and the signature. Nothing is taken on trust. '
             '<a href="/trust">How this works</a></span><ol class="verify-out" hidden></ol></div>')
    body = f"""
<div class="cert-head"><div>
<p class="muted" style="margin:0">Repair certificate{", live chain" if chain == "live" else ""}</p>
<h1 style="margin:4px 0 12px">{seq:06d}</h1>
<p class="lede">{E(title or cert.get("signature") or "")}</p>
<p class="faint small">Written {E(cert["issued_at"].replace("T", " at "))}{bug_link}</p>
</div>{V.seal(cert["hash"], 104)}</div>
<ol class="steps">
<li><h2>An error was caught</h2>
<p>A visit to <code>{E(req.get("path", ""))}</code> crashed the app with a
<b>{E(capture.get("exception_type", ""))}</b> error. Kintsugi saved everything
about that visit so it could study it.</p></li>
<li><h2>The error was repeated on purpose</h2>
<div class="replays" aria-hidden="true">{"".join("<span></span>" for _ in verdicts)}</div>
<p>In a safe copy of the app, Kintsugi replayed the same visit {len(verdicts)}
times and it crashed every time. If you can make an error happen whenever you
like, you can test whether a fix really stops it.</p></li>
<li><h2>The AI suggested fixes</h2>
<ul style="padding-left:18px;margin:0">{"".join(cands)}</ul></li>
<li><h2>The fix that went live</h2>
<p class="muted small">Red lines were removed, green lines were added.</p>
<pre>{V.diff_html(cert.get("patch", ""))}</pre></li>
<li><h2>It was signed and added to the chain</h2>
<p>Kintsugi signed this record and linked it to the certificate before it,
so nobody can quietly change it later.</p>
<p class="faint small">ID <span class="mono">{cert["hash"][:16]}</span>,
linked to <span class="mono">{cert["prev_hash"][:16]}</span></p>{replay}</li>
</ol>{check}<p class="links">{"".join(links)}</p>"""
    return page(f"Certificate {seq:06d}", "certificates", body,
                extra_head='<script type="module" src="/static/verify.js"></script>')


# --------------------------------------------------------------- practice

@app.get("/practice", response_class=HTMLResponse)
def practice():
    rows = PracticeBank(BANK_PATH).all()
    if not rows:
        return page("Practice", "practice",
                    "<h1>Practice</h1><p class=lede>No practice yet. When the "
                    "app is quiet, Kintsugi breaks a private copy of it on "
                    "purpose and practises fixing it. That practice will show "
                    "up here.</p>")
    bugs = {r["entry_id"] for r in rows}
    would = sum(r["status"] == "would_heal" for r in rows)
    alone = sum(r["status"] == "refused" for r in rows)
    tests = sum(bool(r.get("regression_test")) for r in rows)
    as_outcome = {"would_heal": "recovered", "refused": "refused",
                  "escalated": "escalated", "no_fire": "fault_never_fired"}
    t = titles()
    table = "".join(
        f'<tr><td class="mono">{E(r["entry_id"])}</td>'
        f'<td class="muted small">{E(t.get(r["entry_id"], ""))}</td>'
        f'<td>{V.status(as_outcome.get(r["status"], r["status"]))}</td>'
        f'<td class="faint small num">{E((r.get("rehearsed_at") or "").replace("T", " "))}</td>'
        f'<td class="faint small">{E(D.plain_reason(r.get("reason") or ""))}</td></tr>'
        for r in reversed(rows))
    body = ("<h1>Practice</h1>"
            "<p class=lede>When the app is quiet, Kintsugi breaks a private copy "
            "of it on purpose and practises fixing it, so it has seen a problem "
            "before it happens for real. Every practice that ends in a fix "
            "leaves behind a test that stops the same bug coming back.</p>"
            '<div class="stats">'
            f"<div><b>{len(rows)}</b><span>practice runs</span></div>"
            f"<div><b>{len(bugs)}</b><span>bugs practised</span></div>"
            f"<div><b>{would}</b><span>would be fixed</span></div>"
            f"<div><b>{alone}</b><span>would be left alone</span></div>"
            f"<div><b>{tests}</b><span>tests saved</span></div></div>"
            '<div class="table-scroll"><table class="ex-table"><thead><tr>'
            '<th scope="col">Bug</th><th scope="col">What was wrong</th>'
            '<th scope="col">Practice result</th><th scope="col" class="num">When</th>'
            f'<th scope="col">Note</th></tr></thead><tbody>{table}</tbody></table></div>')
    return page("Practice", "practice", body)


# ------------------------------------------------------- connected heal jobs

def _owned_job(request: Request, job_id: int):
    """The job, if the signed-in person owns its service (or is an admin)."""
    who = auth.identity(request)
    if not who:
        return None, auth.login_redirect(request)
    job = H.get_job(job_id)
    if job is None:
        return None, page("Not found", "account", "<h1>Not found</h1>", status=404)
    if is_admin(request):
        return job, None
    user = A.get_user(who["u"]) if who["r"] == "user" else None
    if user is None or job.service_id not in {s.id for s in A.user_services(user.id)}:
        return None, page("Not found", "account", "<h1>Not found</h1>", status=404)
    return job, None


@app.get("/account/incidents/{job_id}", response_class=HTMLResponse)
def heal_job_page(job_id: int, request: Request):
    job, deny = _owned_job(request, job_id)
    if deny:
        return deny
    H.mark_seen(job.id)
    from control_plane.db import Incident, get_session
    with get_session() as s:
        inc = s.get(Incident, job.incident_id)
    svc = A.service_by_id(job.service_id)
    return page("Incident", "account",
                P.heal_job(job, inc, svc.name if svc else "service", H.artifacts(job.id)))


@app.get("/account/artifacts/{artifact_id}")
def heal_artifact(artifact_id: int, request: Request):
    art = H.get_artifact(artifact_id)
    if art is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    job, deny = _owned_job(request, art.job_id)
    if deny:
        return deny
    return Response(art.data, media_type=art.content_type,
                    headers={"Cache-Control": "private, max-age=3600"})


@app.get("/account/incidents/{job_id}/certificate.json")
def heal_certificate(job_id: int, request: Request):
    job, deny = _owned_job(request, job_id)
    if deny:
        return deny
    if not job.certificate_json:
        return JSONResponse({"error": "no certificate yet"}, status_code=404)
    return JSONResponse(json.loads(job.certificate_json))


@app.post("/account/services/{service_id}/repo")
async def service_repo(service_id: int, request: Request):
    who = auth.identity(request)
    form = await auth.read_form(request)
    if not who or who["r"] != "user" or not auth.check_form_token(form.get("csrf", ""), "service"):
        return RedirectResponse("/account", status_code=303)
    user = A.get_user(who["u"])
    if user is None or service_id not in {s.id for s in A.user_services(user.id)}:
        return RedirectResponse("/account", status_code=303)
    try:
        H.set_repo(service_id, form.get("repo", ""), form.get("branch", "main").strip())
    except ValueError as exc:
        return _account_page(request, user, service_error=str(exc), status=400)
    return RedirectResponse("/account#services", status_code=303)


def _worker(request: Request) -> bool:
    return H.worker_ok(request.headers.get("x-kintsugi-worker", ""))


@app.get("/worker/jobs")
def worker_jobs(request: Request):
    if not _worker(request):
        return JSONResponse({"error": "worker token required"}, status_code=401)
    return {"jobs": H.pending(), "spent_usd": H.total_cost()}


@app.post("/worker/services")
async def worker_connect_service(request: Request):
    """Connect a service on an owner account (owners only), returning its
    ingest URL once; the owner's own worker uses this to wire up their apps."""
    if not _worker(request):
        return JSONResponse({"error": "worker token required"}, status_code=401)
    body = await request.json()
    email = next((e for e in body.get("emails", []) if A.is_admin(e) and A.get_user(e)), None)
    if email is None:
        return JSONResponse({"error": "no owner account with those emails"}, status_code=404)
    user = A.get_user(email)
    try:
        svc, key = A.create_service(user.id, body.get("name", ""))
        if body.get("repo"):
            H.set_repo(svc.id, body["repo"], body.get("branch") or "main")
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    base = os.environ.get("KINTSUGI_PUBLIC_URL", str(request.base_url).rstrip("/"))
    return {"account": email, "service": svc.name, "connect": f"{base}/connect/{key}"}


@app.post("/worker/jobs/{job_id}/stage")
async def worker_stage(job_id: int, request: Request):
    if not _worker(request):
        return JSONResponse({"error": "worker token required"}, status_code=401)
    body = await request.json()
    try:
        job = H.advance(job_id, body["stage"], body.get("note", ""),
                        explanation=body.get("explanation"), patch=body.get("patch"),
                        pr_url=body.get("pr_url"), cost_usd=body.get("cost_usd"))
    except (KeyError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"job": job.id, "stage": job.stage}


@app.post("/worker/jobs/{job_id}/artifact")
async def worker_artifact(job_id: int, request: Request):
    if not _worker(request):
        return JSONResponse({"error": "worker token required"}, status_code=401)
    data = await request.body()
    try:
        aid = H.add_artifact(job_id, request.query_params.get("kind", "file"),
                             request.headers.get("content-type", "application/octet-stream"), data)
    except (KeyError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"artifact": aid}


@app.post("/worker/jobs/{job_id}/certify")
async def worker_certify(job_id: int, request: Request):
    if not _worker(request):
        return JSONResponse({"error": "worker token required"}, status_code=401)
    try:
        cert = H.certify(job_id, await request.json())
    except KeyError as exc:
        return JSONResponse({"error": f"no job {exc}"}, status_code=404)
    return {"sequence": cert["sequence"], "hash": cert["hash"]}


# ----------------------------------------------------------------- errors

@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    if not is_admin(request):
        if auth.current_user(request):
            return page("Admins only", "admin",
                        "<h1>Admins only</h1><p class=lede>This page is for the "
                        "project's owner. Your account can run real heals on the "
                        '<a href="/live">Live</a> page.</p>', status=403)
        return auth.login_redirect(request)
    try:
        from control_plane.api.main import list_incidents
        errors = len(list_incidents())
    except Exception:
        errors = None
    return page("Admin", "admin",
                P.admin(A.site_counts(), errors, A.recent_runs(72, 40), titles()))


@app.get("/incidents", response_class=HTMLResponse)
def incidents(request: Request):
    if not is_admin(request):
        if auth.current_user(request):
            return page("Admins only", "errors",
                        "<h1>Admins only</h1><p class=lede>The live error feed is "
                        "for the project's owner. Your account can run real heals "
                        'on the <a href="/live">Live</a> page.</p>', status=403)
        return auth.login_redirect(request)
    try:
        from control_plane.api.main import list_incidents
        rows = list_incidents()
    except Exception as exc:
        return page("Errors", "errors",
                    "<h1>Errors</h1><p class=lede>The list of errors could not "
                    f"be loaded ({E(str(exc))}). Check the database connection.</p>")
    if not rows:
        return page("Errors", "errors",
                    "<h1>Errors</h1><p class=lede>No errors so far. When the "
                    "live app hits one, it will show up here.</p>")
    table = "".join(
        f'<tr><td class="num">{r["id"]}</td><td>{E(r["service"])}</td>'
        f'<td>{E(r["exception_type"])}</td><td class="mono">{E(r["release"])}</td>'
        f'<td class="num">{r["capture_count"]}</td><td>{E(r["status"])}</td>'
        f'<td class="faint small num">{E(str(r["created_at"]).replace("T", " ")[:19])}</td></tr>'
        for r in rows)
    body = ("<h1>Errors</h1><p class=lede>Errors reported by the live app. When "
            "the same error happens many times, it is counted as one problem, so "
            "a burst of errors is one thing to fix, not hundreds.</p>"
            '<div class="table-scroll"><table class="ex-table"><thead><tr>'
            '<th scope="col" class="num">Number</th><th scope="col">Part of the app</th>'
            '<th scope="col">Error</th><th scope="col">Version</th>'
            '<th scope="col" class="num">Times seen</th><th scope="col">Status</th>'
            f'<th scope="col" class="num">First seen</th></tr></thead><tbody>{table}</tbody>'
            '</table></div><p class="faint small">Raw data: '
            '<a href="/api/incidents">/api/incidents</a></p>')
    return page("Errors", "errors", body)
