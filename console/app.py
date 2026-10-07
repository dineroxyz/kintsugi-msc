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
                               RedirectResponse, StreamingResponse)
from fastapi.staticfiles import StaticFiles

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
                and not auth.current_user(request)):
            return JSONResponse({"error": "sign in required"}, status_code=401)
        response = await call_next(request)
    finally:
        _request.reset(token)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


def page(title: str, active: str, body: str, status: int = 200,
         wide: bool = False, extra_head: str = "") -> HTMLResponse:
    request = _request.get()
    user = auth.current_user(request) if request else None
    html = V.shell(title, active, body, user=user, wide=wide,
                   csrf=auth.form_token("logout") if user else "",
                   extra_head=extra_head)
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
                       hero_replay=rec["name"] if rec else None, hero_meta=rec),
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
def login_form(request: Request, next: str = "/results"):
    if auth.current_user(request):
        return RedirectResponse(auth.safe_next(next), status_code=303)
    return page("Sign in", "login",
                P.login(auth.form_token("login"), auth.safe_next(next),
                        configured=auth.configured(),
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
                            configured=auth.configured(),
                            locked=auth.locked_for(ip)), status=status)

    if not auth.check_form_token(form.get("csrf", ""), "login"):
        return again("This form expired. Please try again.", 400)
    if not auth.configured():
        return again("", 403)
    if auth.locked_for(ip):
        return again("", 429)
    if not auth.check_credentials(form.get("user", ""), form.get("password", "")):
        auth.record_failure(ip)
        return again("That user name and password do not match. Check them "
                     "and try again.", 401)
    auth.clear_failures(ip)
    response = RedirectResponse(nxt, status_code=303)
    auth.start_session(response, request, auth.admin_user())
    return response


@app.post("/logout")
async def logout(request: Request):
    form = await auth.read_form(request)
    response = RedirectResponse("/", status_code=303)
    if auth.check_form_token(form.get("csrf", ""), "logout"):
        auth.end_session(response)
    return response


# ------------------------------------------------------------------- live

def _live_allowed(request: Request) -> bool:
    """Live runs cost model calls and start processes: when an operator
    password is set, only the signed-in operator may start one."""
    return L.enabled() and (not auth.configured() or bool(auth.current_user(request)))


@app.get("/live", response_class=HTMLResponse)
def live_page(request: Request):
    from corpus import load_corpus
    try:
        entries = load_corpus()
    except Exception:
        entries = []
    os.environ.setdefault("KINTSUGI_CERT_DIR", CERT_DIR)
    body = P.live(entries, L.recordings(), live_enabled=L.enabled(),
                  repairs=L.repair_count(), csrf=auth.form_token("live"),
                  signed_in=bool(auth.current_user(request)),
                  needs_sign_in=auth.configured())
    return page("Break it, and watch it mend", "live", body, wide=True,
                extra_head='<link rel="stylesheet" href="/static/live.css">\n'
                           '<script src="/static/live.js" defer></script>')


@app.post("/live/run")
async def live_run(request: Request):
    form = await auth.read_form(request)
    if not auth.check_form_token(form.get("csrf", ""), "live"):
        return JSONResponse({"error": "This page expired. Reload it and try again."},
                            status_code=400)
    if not _live_allowed(request):
        return JSONResponse({"error": "Live runs are not available here."}, status_code=403)
    if L.busy():
        return JSONResponse({"error": "A run is already in progress."}, status_code=409)
    entry = form.get("entry", "")
    try:
        run = L.start(entry)
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)
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


@app.get("/data/verification.json")
def verification_json():
    if not os.path.exists(VERIFY_PATH):
        return JSONResponse({"error": "no verification report yet"},
                            status_code=404)
    return FileResponse(VERIFY_PATH, media_type="application/json")


# ----------------------------------------------------------- certificates

@app.get("/certificates", response_class=HTMLResponse)
def certificates(page_no: int = 1):
    store = CertificateStore(CERT_DIR)
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
                    "<h1>Repair certificates</h1><p class=lede>No certificates "
                    "yet. Each time Kintsugi puts a fix live, it writes a "
                    "certificate, and it will show up here.</p>")

    issued_by = {h: t for t in trials()
                 for h in (t.get("detail") or {}).get("certificates", [])}
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
            f'<li>{V.seal(c["hash"])}<div><a href="/certificates/{c["sequence"]}">'
            f'Certificate {c["sequence"]:06d}</a>'
            f'<div class="meta">{E(". ".join(ctx))}</div>'
            f'<div class="meta">Written {E(c["issued_at"].replace("T", " at "))}, '
            f'ID <span class="mono">{c["hash"][:16]}</span></div></div></li>')
    nav = ['<nav class="pager" aria-label="Pages">']
    nav.append(f'<a class="btn" href="/certificates?page_no={page_no - 1}">Newer</a>'
               if page_no > 1 else '<span class="btn" aria-disabled="true">Newer</span>')
    nav.append(f'<span class="muted small">Page {page_no} of {pages}</span>')
    nav.append(f'<a class="btn" href="/certificates?page_no={page_no + 1}">Older</a>'
               if page_no < pages else '<span class="btn" aria-disabled="true">Older</span>')
    nav.append("</nav>")
    body = ("<h1>Repair certificates</h1>"
            "<p class=lede>Each time Kintsugi puts a fix live, it writes a "
            "certificate: what went wrong, proof that the error could be "
            "repeated, every fix the AI tried, and which checks the final fix "
            "passed. Each certificate is linked to the one before it, like a "
            "chain, so if anyone changes or deletes one, it shows.</p>"
            f'{banner}<ol class="ledger">{"".join(items)}</ol>{"".join(nav)}')
    return page("Certificates", "certificates", body)


COND_NAME = {"structural": "Not just hiding the error",
             "applies": "Fix is written correctly",
             "reproduction": "The error is gone",
             "suite": "Nothing else broke"}


@app.get("/certificates/{seq}", response_class=HTMLResponse)
def certificate(seq: int):
    certs = {c["sequence"]: c for c in CertificateStore(CERT_DIR).all()}
    cert = certs.get(seq)
    if cert is None:
        return not_found("No such certificate", "/certificates",
                         "See all certificates")
    t = next((x for x in trials()
              if cert["hash"] in (x.get("detail") or {}).get("certificates", [])), {})
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

    ver = verification().get("by_sequence", {}).get(cert["sequence"])
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
        links.append(f'<a href="/certificates/{seq - 1}">Previous certificate</a>')
    links.append('<a href="/certificates">All certificates</a>')
    if seq + 1 in certs:
        links.append(f'<a href="/certificates/{seq + 1}">Next certificate</a>')
    bug_link = (f', <a href="/bugs/{E(t["entry"])}">bug {E(t["entry"])}</a>'
                if t.get("entry") else "")

    body = f"""
<div class="cert-head"><div>
<p class="muted" style="margin:0">Repair certificate</p>
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
</ol><p class="links">{"".join(links)}</p>"""
    return page(f"Certificate {seq:06d}", "certificates", body)


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


# ----------------------------------------------------------------- errors

@app.get("/incidents", response_class=HTMLResponse)
def incidents(request: Request):
    if not auth.current_user(request):
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
