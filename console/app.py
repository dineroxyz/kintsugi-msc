"""Kintsugi operations console.

Server-rendered, no build step: the study results, the certificate chain,
the practice bank, and the incident stream, each read straight from the
artefacts the platform writes. Mounts the control plane API under /api so
one deployment carries both.

    uvicorn console.app:app --port 8100
"""

from __future__ import annotations

import html
import json
import os
import statistics

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from control_plane.api.main import app as control_plane_api
from control_plane.certificates.issuer import (CertificateError,
                                               CertificateStore)
from control_plane.practice.mode import PracticeBank

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUDY_DIR = os.environ.get("KINTSUGI_STUDY_DIR",
                           os.path.join(ROOT, "study", "pilot"))
CERT_DIR = os.environ.get("KINTSUGI_CERT_DIR",
                          os.path.join(STUDY_DIR, "certificates"))
BANK_PATH = os.environ.get("KINTSUGI_BANK_PATH",
                           os.path.join(ROOT, "study", "practice_bank.jsonl"))

from contextlib import asynccontextmanager


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

STYLE = """
<style>
  :root { --lacquer:#161311; --panel:#211c18; --line:#3a322a;
          --gold:#d4a960; --gold-dim:#8a7345; --ink:#e8e0d2;
          --dim:#9a8f7d; --good:#8fb573; --bad:#c96f5f; --warn:#c9a95f; }
  * { box-sizing:border-box; }
  body { background:var(--lacquer); color:var(--ink); margin:0;
         font:15px/1.55 Georgia, 'Times New Roman', serif;
         /* Georgia defaults to old-style figures, which render "C0" as
            "Co" and "C1" oddly; force full-height lining numerals. */
         font-variant-numeric: lining-nums tabular-nums;
         font-feature-settings: "lnum" 1, "tnum" 1; }
  header { padding:1.4rem 2rem .9rem; border-bottom:1px solid var(--gold-dim); }
  header h1 { margin:0; font-size:1.5rem; letter-spacing:.14em;
              color:var(--gold); font-weight:normal; }
  header h1 span { color:var(--dim); font-size:.9rem; letter-spacing:.04em; }
  nav { padding:.5rem 2rem; border-bottom:1px solid var(--line); }
  nav a { color:var(--dim); margin-right:1.6rem; text-decoration:none;
          letter-spacing:.06em; }
  nav a:hover, nav a.here { color:var(--gold); }
  main { padding:1.6rem 2rem 3rem; max-width:1100px; }
  h2 { color:var(--gold); font-weight:normal; letter-spacing:.08em;
       border-bottom:1px solid var(--line); padding-bottom:.3rem; }
  .cards { display:flex; gap:1rem; flex-wrap:wrap; margin:1rem 0; }
  .card { background:var(--panel); border:1px solid var(--line);
          border-top:2px solid var(--gold-dim); padding:.8rem 1.1rem;
          min-width:11rem; }
  .card b { display:block; font-size:1.6rem; color:var(--gold);
            font-weight:normal; }
  .card small { color:var(--dim); letter-spacing:.05em; }
  table { border-collapse:collapse; width:100%; margin:.8rem 0; }
  th { text-align:left; color:var(--dim); font-weight:normal;
       letter-spacing:.07em; border-bottom:1px solid var(--gold-dim);
       padding:.3rem .6rem; }
  td { border-bottom:1px solid var(--line); padding:.32rem .6rem; }
  .ok { color:var(--good); } .fail { color:var(--bad); }
  .warn { color:var(--warn); } .dim { color:var(--dim); }
  a { color:var(--gold); }
  pre { background:#0f0d0b; border:1px solid var(--line); padding:1rem;
        overflow-x:auto; font:13px/1.5 ui-monospace, Menlo, monospace; }
  .diff-add { color:var(--good); } .diff-del { color:var(--bad); }
  .seam { color:var(--gold-dim); }
  .lede { color:var(--ink); max-width:60rem; margin:.4rem 0 1.2rem; }
  .legend { background:var(--panel); border:1px solid var(--line);
            padding:.7rem 1.1rem; margin:0 0 1.3rem; max-width:60rem; }
  .legend h3 { color:var(--gold-dim); font-weight:normal; margin:.1rem 0 .5rem;
               font-size:.85rem; letter-spacing:.1em; text-transform:uppercase; }
  .legend dl { display:grid; grid-template-columns:auto 1fr; gap:.2rem .8rem;
               margin:0; }
  .legend dt { color:var(--gold); white-space:nowrap; }
  .legend dd { margin:0; color:var(--dim); }
  .card .armname { color:var(--ink); font-size:.95rem; letter-spacing:0;
                   display:block; margin-top:.1rem; }
  .help { color:var(--gold-dim); cursor:help; border-bottom:1px dotted var(--gold-dim); }
</style>
"""


def page(title: str, active: str, body: str) -> HTMLResponse:
    tabs = [("/", "overview", "Overview"),
            ("/certificates", "certificates", "Certificates"),
            ("/practice", "practice", "Practice log"),
            ("/api/incidents", "incidents", "Live incidents")]
    nav = "".join(
        f'<a href="{href}" class="{"here" if key == active else ""}">{label}</a>'
        for href, key, label in tabs)
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(title)} — Kintsugi</title>{STYLE}</head><body>
<header><h1>KINTSUGI <span>— repairs under proof · practises while idle ·
gets harder to break</span></h1></header>
<nav>{nav}</nav><main>{body}</main></body></html>"""
    return HTMLResponse(doc)


def load_trials() -> list[dict]:
    path = os.path.join(STUDY_DIR, "trials.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path)]


OUTCOME_CLASS = {"recovered": "ok", "false_fix": "fail", "refused": "warn",
                 "escalated": "warn", "unrecovered": "dim",
                 "harness_error": "fail", "fault_never_fired": "dim"}

# plain-language names, shown next to the study codes
ARM_NAME = {
    "C0": "No recovery (baseline)",
    "C1": "Restart, then roll back",
    "C2": "AI repair, no safety check",
    "C3": "AI repair behind the gate",
}
ARM_BLURB = {
    "C0": "Watches the failure and does nothing. The yardstick.",
    "C1": "The traditional runbook: restart the service, and if that fails, "
          "roll back to the previous version.",
    "C2": "Lets the model write a patch and ships it once tests pass, with no "
          "requirement to reproduce the failure first.",
    "C3": "Kintsugi. Reproduces the failure, has the model write a patch, and "
          "ships only what passes the reproduction, the tests and a structural "
          "check.",
}
# plain words for the outcomes, with the technical term on hover
OUTCOME_LABEL = {
    "recovered": ("fixed", "the failing request works again afterwards"),
    "false_fix": ("bad fix shipped", "a patch was deployed but the failure "
                  "is still there"),
    "refused": ("declined", "the platform would not act because it could not "
                "reproduce the failure reliably"),
    "escalated": ("gave up", "no safe patch was found; handed back to a human"),
    "unrecovered": ("still broken", "the failure was left live"),
    "harness_error": ("run error", "the test harness itself errored"),
    "fault_never_fired": ("did not trigger", "the injected fault never "
                          "surfaced under load"),
}


def outcome_cell(outcome: str) -> str:
    label, tip = OUTCOME_LABEL.get(outcome, (outcome, ""))
    cls = OUTCOME_CLASS.get(outcome, "")
    return (f'<td class="{cls}"><span class="help" title="{html.escape(tip)} '
            f'(technical term: {outcome})">{label}</span></td>')


def arms_legend(arms: list[str]) -> str:
    rows = "".join(
        f'<dt>{arm} — {ARM_NAME.get(arm, arm)}</dt>'
        f'<dd>{ARM_BLURB.get(arm, "")}</dd>'
        for arm in arms)
    return ('<div class="legend"><h3>The four approaches compared</h3>'
            f'<dl>{rows}</dl></div>')


@app.get("/", response_class=HTMLResponse)
def overview():
    trials = load_trials()
    if not trials:
        return page("Overview", "overview",
                    "<h2>Study</h2><p class=lede>No results yet. Once a study "
                    "run has produced results, they appear here.</p>")

    arms = sorted({t["arm"] for t in trials})
    cards = []
    for arm in arms:
        rows = [t for t in trials if t["arm"] == arm]
        recovered = sum(1 for r in rows if r["outcome"] == "recovered")
        false_fix = sum(1 for r in rows if r["outcome"] == "false_fix")
        mttrs = [r["mttr_seconds"] for r in rows if r["mttr_seconds"]]
        med = f"{statistics.median(mttrs):.1f}s" if mttrs else "n/a"
        ff = (f'<span class=fail>{false_fix} bad fixes shipped</span>'
              if false_fix else "no bad fixes shipped")
        cards.append(
            f'<div class="card"><small>{arm}</small>'
            f'<span class="armname">{ARM_NAME.get(arm, arm)}</span>'
            f'<b>{recovered}/{len(rows)}</b>'
            f'<small>failures fixed · typical time to fix {med}<br>{ff}</small>'
            f'</div>')

    rows_html = "".join(
        f'<tr><td>{t["entry"]}</td>'
        f'<td><span class="help" title="{html.escape(ARM_NAME.get(t["arm"], t["arm"]))}">{t["arm"]}</span></td>'
        f'{outcome_cell(t["outcome"])}'
        f'<td>{t["mttr_seconds"] if t["mttr_seconds"] is not None else "—"}</td>'
        f'<td class=dim>{"should refuse" if t.get("expected_c3") == "refused" else "should fix"}</td>'
        f'<td class=dim>{html.escape(str(t.get("detail", {}).get("heal_reason", ""))[:90])}</td></tr>'
        for t in trials)
    body = (f"<h2>Study results — {len(trials)} test runs</h2>"
            '<p class="lede">Each fault was injected into a live service and '
            "handed to four different recovery approaches. A run counts as "
            "<b>fixed</b> only when the failing request works again "
            "<i>and</i> live traffic is clean; time to fix is measured from "
            "the first error to the moment it recovers.</p>"
            f'{arms_legend(arms)}'
            f'<div class="cards">{"".join(cards)}</div>'
            '<table><tr><th>Fault</th><th>Approach</th><th>Result</th>'
            '<th>Time to fix (s)</th><th>Expected</th><th>Notes</th></tr>'
            f"{rows_html}</table>")
    return page("Overview", "overview", body)


@app.get("/certificates", response_class=HTMLResponse)
def certificates():
    store = CertificateStore(CERT_DIR)
    try:
        chain = store.verify_chain()
        status = ('<p class="ok">All certificates verified — every record is '
                  'intact and none has been altered.</p>')
    except CertificateError as exc:
        chain = store.all()
        status = (f'<p class="fail">A certificate failed verification: '
                  f'{html.escape(str(exc))}</p>')

    rows = "".join(
        f'<tr><td><a href="/certificates/{c["sequence"]}">'
        f'#{c["sequence"]:06d}</a></td>'
        f'<td>{html.escape(c.get("signature") or "")}</td>'
        f'<td class=dim>{c["issued_at"]}</td>'
        f'<td class=seam>{c["hash"][:16]}…</td></tr>'
        for c in chain)
    body = (f"<h2>Repair certificates — {len(chain)}</h2>"
            '<p class="lede">Every fix the platform ships is recorded as a '
            "tamper-evident certificate: the failure, how it was reproduced, "
            "the patch, and the checks it passed. The records are chained "
            "together, so altering any one of them is detectable.</p>"
            f"{status}"
            "<table><tr><th>Number</th><th>Failure</th><th>Issued</th>"
            f"<th>Fingerprint</th></tr>{rows}</table>")
    return page("Certificates", "certificates", body)


@app.get("/certificates/{seq}", response_class=HTMLResponse)
def certificate(seq: int):
    store = CertificateStore(CERT_DIR)
    certs = {c["sequence"]: c for c in store.all()}
    cert = certs.get(seq)
    if cert is None:
        return page("Certificate", "certificates",
                    "<h2>No such certificate</h2>")

    diff_html = "".join(
        f'<span class="{"diff-add" if line.startswith("+") else "diff-del" if line.startswith("-") else ""}">'
        f"{html.escape(line)}</span>\n"
        for line in cert["patch"].splitlines())
    conditions = cert["verification_record"]["payload"]
    verdicts = cert["reproduction"].get("verdicts") or []
    body = f"""
<h2>Certificate {seq:06d} <span class=seam>· {cert['hash'][:20]}…</span></h2>
<div class="cards">
  <div class="card"><small>signature</small>
    <b style="font-size:1rem">{html.escape(cert.get('signature') or '')}</b></div>
  <div class="card"><small>issued</small>
    <b style="font-size:1rem">{cert['issued_at']}</b></div>
  <div class="card"><small>reproduction</small>
    <b style="font-size:1rem">{html.escape(cert['reproduction']['reason'])}</b>
    <small>{len(verdicts)} replays</small></div>
  <div class="card"><small>candidates seen</small>
    <b style="font-size:1rem">{len(cert['candidates'])}</b></div>
</div>
<h2>Admitted patch</h2><pre>{diff_html}</pre>
<h2>Gate record</h2>
<pre>{html.escape(json.dumps(conditions, indent=2))}</pre>
<p class=dim>Replay this repair from the certificate alone:
<code>replay_certificate(cert, faulty_tree)</code> — reproduces the captured
failure on the pre-patch tree and proves the embedded patch silences it.</p>
<p class=seam>prev {cert['prev_hash'][:20]}… → this {cert['hash'][:20]}…</p>"""
    return page(f"Certificate {seq}", "certificates", body)


@app.get("/practice", response_class=HTMLResponse)
def practice():
    bank = PracticeBank(BANK_PATH)
    rows = bank.all()
    if not rows:
        return page("Practice", "practice",
                    "<h2>Practice log</h2><p class=lede>Nothing here yet. "
                    "When the platform is idle it rehearses failures against "
                    "a copy of the services; those rehearsals will show up "
                    "here.</p>")
    counts = bank.counts()
    would = sum(1 for r in rows if r["status"] == "would_heal")
    refused = sum(1 for r in rows if r["status"] == "refused")
    cards = (f'<div class="cards">'
             f'<div class="card"><small>rehearsals run</small><b>{len(rows)}</b></div>'
             f'<div class="card"><small>would fix</small><b class=ok>{would}</b></div>'
             f'<div class="card"><small>would decline</small><b>{refused}</b></div>'
             f'<div class="card"><small>faults covered</small><b>{len(counts)}</b></div>'
             f'</div>')
    status_word = {"would_heal": ("would fix", "ok"),
                   "refused": ("would decline", "warn"),
                   "no_fire": ("did not trigger", "dim")}
    table = "".join(
        f'<tr><td>{r["entry_id"]}</td>'
        f'<td class="{status_word.get(r["status"], (r["status"], ""))[1]}">'
        f'{status_word.get(r["status"], (r["status"], ""))[0]}</td>'
        f'<td class=dim>{html.escape(r.get("key") or "")}</td>'
        f'<td class=dim>{r["rehearsed_at"]}</td>'
        f'<td class=dim>{html.escape((r.get("reason") or "")[:80])}</td></tr>'
        for r in reversed(rows))
    body = ("<h2>Practice log</h2>"
            '<p class="lede">While idle, the platform breaks a private copy '
            "of the services on purpose and rehearses the repair, so it has "
            "seen the failure before it happens for real. Each rehearsal is "
            "recorded below.</p>"
            f"{cards}"
            '<table><tr><th>Fault</th><th>Rehearsal result</th>'
            "<th>Failure shape</th><th>When</th><th>Notes</th></tr>"
            f"{table}</table>")
    return page("Practice", "practice", body)
