"""Kintsugi operations console.

Server-rendered, no build step: the study results, the certificate chain,
the practice bank, and the incident stream, each read straight from the
artefacts the platform writes. Mounts the control plane API under /api so
one deployment carries both.

Design: light mode is stoneware (ash-glaze surface, urushi ink, gold only
for seams and for "fixed"); dark mode is the lacquer theme. The status
palette was checked with the dataviz validator in both modes (all checks
pass) and every status also carries a word, and "gave up" a texture, so
colour is never the only cue.

    uvicorn console.app:app --port 8100
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import statistics
from contextlib import asynccontextmanager
from functools import lru_cache

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

E = html.escape

# ------------------------------------------------------------------ style

STYLE = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@500;700&family=Zen+Kaku+Gothic+New:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root {
  --glaze:#E4E7E1; --slip:#F3F4F0; --ink:#2B2420; --clay:#685C53;
  --rule:#C9CCC4; --seam:#B0842F; --seal:#B8432C;
  --fixed:#B0842F; --declined:#2F5FA8; --gaveup:#2A9070; --broken:#B8432C;
  --code-bg:#ECEEE8; --add:#2A7A4E; --del:#A53A27;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --glaze:#171310; --slip:#1E1915; --ink:#EAE2D4; --clay:#A79B8A;
    --rule:#3A322A; --seam:#B88D3E; --seal:#C7464F;
    --fixed:#B88D3E; --declined:#5F86C9; --gaveup:#2FA680; --broken:#C7464F;
    --code-bg:#14110E; --add:#7DBB8E; --del:#E07A68;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --glaze:#171310; --slip:#1E1915; --ink:#EAE2D4; --clay:#A79B8A;
  --rule:#3A322A; --seam:#B88D3E; --seal:#C7464F;
  --fixed:#B88D3E; --declined:#5F86C9; --gaveup:#2FA680; --broken:#C7464F;
  --code-bg:#14110E; --add:#7DBB8E; --del:#E07A68;
  color-scheme: dark;
}
* { box-sizing:border-box; }
html { -webkit-text-size-adjust:100%; }
body { margin:0; background:var(--glaze); color:var(--ink);
       font:16px/1.65 "Zen Kaku Gothic New", system-ui, sans-serif;
       font-variant-numeric: lining-nums tabular-nums; }
a { color:inherit; text-decoration-color:var(--seam);
    text-underline-offset:3px; }
a:hover { text-decoration-thickness:2px; }
:focus-visible { outline:2px solid var(--seam); outline-offset:3px; }
.wrap { max-width:1120px; margin:0 auto; padding:0 16px; }
@media (min-width:720px) { .wrap { padding:0 40px; } }

header.site { padding:28px 0 0; }
.bar { display:flex; flex-wrap:wrap; align-items:baseline;
       justify-content:space-between; gap:12px 32px; }
.mark { font-family:"Shippori Mincho", serif; font-weight:700;
        font-size:30px; letter-spacing:.02em; text-decoration:none; }
nav.tabs { display:flex; flex-wrap:wrap; gap:4px 22px; font-size:15px; }
nav.tabs a { text-decoration:none; color:var(--clay); padding:2px 0;
             border-bottom:2px solid transparent; }
nav.tabs a:hover { color:var(--ink); }
nav.tabs a[aria-current="page"] { color:var(--ink);
             border-bottom-color:var(--seam); }
.theme { background:none; border:1px solid var(--rule); color:var(--clay);
         font:inherit; font-size:13px; padding:2px 10px; border-radius:20px;
         cursor:pointer; }
.seamline { display:block; width:100%; height:22px; margin-top:14px; }
.seamline path { fill:none; stroke:var(--seam); stroke-width:1.6;
                 stroke-linecap:round; stroke-linejoin:round; }

main { padding:28px 0 72px; }
h1, h2, h3 { font-family:"Shippori Mincho", serif; font-weight:700;
             line-height:1.25; }
h1 { font-size:clamp(28px, 4.2vw, 42px); margin:8px 0 14px; max-width:22ch; }
h2 { font-size:24px; margin:56px 0 10px; }
h3 { font-size:18px; margin:28px 0 8px; }
p { max-width:68ch; margin:0 0 14px; }
.lede { font-size:18px; color:var(--ink); }
.quiet { color:var(--clay); }
.small { font-size:14px; }

.panel { background:var(--slip); border:1px solid var(--rule);
         border-radius:6px; padding:20px; }
.scroll { overflow-x:auto; -webkit-overflow-scrolling:touch; }
.scroll > table { min-width:640px; }
.banner svg, .banner .sw { flex:none; }

table { border-collapse:collapse; width:100%; font-size:15px; }
th { text-align:left; font-weight:500; color:var(--clay); font-size:14px;
     padding:8px 12px 8px 0; border-bottom:1px solid var(--ink); }
td { padding:9px 12px 9px 0; border-bottom:1px solid var(--rule);
     vertical-align:top; }
td.num, th.num { text-align:right; }
tr:last-child td { border-bottom:none; }
.fig { font-family:"Shippori Mincho", serif; font-size:22px; font-weight:700; }

.status { display:inline-flex; align-items:center; gap:7px; white-space:nowrap; }
.sw { width:11px; height:11px; border-radius:2px; flex:none; }
.sw.fixed { background:var(--fixed); }
.sw.declined { background:var(--declined); }
.sw.gaveup { background:repeating-linear-gradient(135deg, var(--gaveup) 0 2px,
             transparent 2px 4px); outline:1.5px solid var(--gaveup);
             outline-offset:-1.5px; }
.sw.broken { background:var(--broken); }
.sw.none { background:transparent; outline:1.5px solid var(--clay);
           outline-offset:-1.5px; }

.legend { display:flex; flex-wrap:wrap; gap:6px 22px; font-size:14px;
          margin:12px 0 0; padding:0; list-style:none; }

.matrix text { font-family:"Zen Kaku Gothic New", sans-serif;
               fill:var(--clay); }
.matrix .lab { fill:var(--ink); font-size:13px; }
.matrix .grp { fill:var(--ink); font-size:13px; font-weight:500; }
.matrix .fid { font-size:9px; }
.matrix .c-fixed { fill:var(--fixed); }
.matrix .c-declined { fill:var(--declined); }
.matrix .c-gaveup { fill:url(#hatch); }
.matrix .c-broken { fill:var(--broken); }
.matrix .c-none { fill:var(--rule); }
.matrix .hit { fill:transparent; }
.matrix .hit:hover { stroke:var(--ink); stroke-width:1.2; }
.matrix .bracket { stroke:var(--clay); stroke-width:1; fill:none; }

details summary { cursor:pointer; color:var(--clay); font-size:15px;
                  padding:6px 0; }
details[open] summary { margin-bottom:10px; }

pre { background:var(--code-bg); border:1px solid var(--rule);
      border-radius:6px; padding:14px 16px; overflow-x:auto; margin:0;
      font:13px/1.55 ui-monospace, "SF Mono", Menlo, monospace; }
code, .hash { font:13px ui-monospace, "SF Mono", Menlo, monospace; }
.d-add { color:var(--add); } .d-del { color:var(--del); }
.d-hunk { color:var(--clay); }

.ledger { list-style:none; margin:20px 0 0; padding:0; position:relative; }
.ledger::before { content:""; position:absolute; left:19px; top:6px;
                  bottom:6px; width:2px; background:var(--seam); }
.ledger li { position:relative; display:grid;
             grid-template-columns:40px 1fr; gap:14px; padding:10px 0;
             align-items:start; }
.ledger .seal { position:relative; z-index:1; background:var(--glaze); }
.ledger .what { min-width:0; }
.ledger .what a { font-weight:500; }
.ledger .meta { color:var(--clay); font-size:14px; overflow-wrap:anywhere; }

.cert-head { display:grid; grid-template-columns:minmax(0,1fr) auto;
             gap:24px; align-items:start; }
.steps > li, .cert-head > div, .ledger .what { min-width:0; }
code, .hash { overflow-wrap:anywhere; }
th.num { white-space:nowrap; }
.steps { list-style:none; counter-reset:step; margin:28px 0 0; padding:0; }
.steps > li { counter-increment:step; position:relative;
              padding:0 0 30px 46px; border-left:2px solid var(--seam);
              margin-left:15px; }
.steps > li:last-child { border-left-color:transparent; }
.steps > li::before { content:counter(step); position:absolute; left:-16px;
              top:-3px; width:30px; height:30px; border-radius:50%;
              background:var(--slip); border:2px solid var(--seam);
              display:grid; place-items:center;
              font:700 14px "Shippori Mincho", serif; }
.steps h3 { margin:0 0 8px; }
.conds { display:flex; flex-wrap:wrap; gap:6px 16px; margin:6px 0 10px;
         padding:0; list-style:none; font-size:14px; }
.pass::before { content:"\\2713\\00a0"; color:var(--add); font-weight:700; }
.fail::before { content:"\\2717\\00a0"; color:var(--del); font-weight:700; }
.replays { display:flex; gap:5px; margin:6px 0; }
.replays span { width:14px; height:14px; border-radius:50%;
                background:var(--broken); }
.banner { display:flex; gap:12px; align-items:center; padding:12px 16px;
          border-radius:6px; border:1px solid var(--rule);
          background:var(--slip); margin:16px 0; }
.pager { display:flex; gap:16px; align-items:center; margin:18px 0 0;
         font-size:15px; }
.stats { display:flex; flex-wrap:wrap; gap:8px 36px; margin:18px 0 6px; }
.stats div { min-width:120px; }
.stats .fig { display:block; font-size:30px; }
.stats .quiet { font-size:14px; }
@media (max-width:640px) {
  .cert-head { grid-template-columns:minmax(0,1fr); }
  .seamline { margin-top:10px; }
  .cert-head .seal { order:-1; }
  td, th { padding-right:8px; }
}
</style>
"""

THEME_SCRIPT = """
<script>
(function () {
  var t = null;
  try { t = new URLSearchParams(location.search).get("theme") ||
            localStorage.getItem("kintsugi-theme"); } catch (e) {}
  if (t === "light" || t === "dark")
    document.documentElement.setAttribute("data-theme", t);
})();
function kintsugiIsDark() {
  var root = document.documentElement;
  return root.getAttribute("data-theme") === "dark" ||
    (!root.getAttribute("data-theme") &&
     window.matchMedia("(prefers-color-scheme: dark)").matches);
}
function kintsugiLabel() {
  var b = document.querySelector(".theme");
  if (b) b.textContent = kintsugiIsDark() ? "Light mode" : "Dark mode";
}
document.addEventListener("DOMContentLoaded", kintsugiLabel);
function kintsugiTheme() {
  var next = kintsugiIsDark() ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("kintsugi-theme", next); } catch (e) {}
  kintsugiLabel();
}
</script>
"""

# a single hand-drawn seam under the wordmark: the one decorative element
SEAM = ('<svg class="seamline" viewBox="0 0 1000 22" preserveAspectRatio="none" '
        'aria-hidden="true"><path d="M0 11 L140 11 L162 6 L181 14 L203 9 '
        'L240 10 L262 16 L280 11 L520 11 L536 4 L548 13 L566 8 L590 11 '
        'L780 11 L796 17 L812 9 L829 12 L1000 11"/></svg>')


def page(title: str, active: str, body: str) -> HTMLResponse:
    tabs = [("/", "overview", "Study"),
            ("/certificates", "certificates", "Certificates"),
            ("/practice", "practice", "Practice"),
            ("/incidents", "incidents", "Incidents")]
    nav = "".join(
        f'<a href="{href}"{" aria-current=page" if key == active else ""}>'
        f'{label}</a>' for href, key, label in tabs)
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{E(title)} | Kintsugi</title>{STYLE}{THEME_SCRIPT}</head><body>
<header class="site"><div class="wrap">
<div class="bar"><a class="mark" href="/">Kintsugi</a>
<nav class="tabs" aria-label="Sections">{nav}
<button class="theme" type="button" onclick="kintsugiTheme()">Switch theme</button></nav></div>
{SEAM}</div></header>
<main><div class="wrap">{body}</div></main></body></html>"""
    return HTMLResponse(doc)


# ------------------------------------------------------------ vocabulary

ARM_NAME = {
    "C0": "No recovery",
    "C1": "Restart, then roll back",
    "C2": "AI repair, unchecked",
    "C3": "AI repair behind the gate",
}
ARM_BLURB = {
    "C0": "Watches the failure and does nothing. The yardstick.",
    "C1": "The traditional runbook: restart the service, and if the failure "
          "comes back, roll back to the previous release.",
    "C2": "The model writes a patch and it ships once it applies, with no "
          "requirement to reproduce the failure first.",
    "C3": "Kintsugi. Reproduces the failure first, then ships a patch only if "
          "that reproduction now passes, along with the tests and a check for "
          "cheating patches.",
}
VARIANT_NAME = {
    "C3-noguard": "Gate removed",
    "C3-noregen": "One attempt only, no retries",
    "C3-nomemory": "Repeat run of the full gate",
}
# outcome -> (status key, plain word, explanation)
OUTCOME = {
    "recovered": ("fixed", "Fixed",
                  "the failing request works again and live traffic is clean"),
    "refused": ("declined", "Declined",
                "the platform could not make the failure happen again on "
                "demand, so it would not act"),
    "escalated": ("gaveup", "Gave up",
                  "no patch passed the checks within three attempts; handed "
                  "to a person"),
    "unrecovered": ("broken", "Still broken",
                    "nothing was done and the failure stayed live"),
    "false_fix": ("broken", "Bad fix shipped",
                  "a patch was deployed but the failure is still there"),
    "harness_error": ("none", "Run error", "the test harness itself failed"),
    "fault_never_fired": ("none", "Did not trigger",
                          "the injected fault never surfaced under load"),
}


def status(outcome: str) -> str:
    key, word, tip = OUTCOME.get(outcome, ("none", outcome, ""))
    return (f'<span class="status" title="{E(tip)} (recorded as {E(outcome)})">'
            f'<span class="sw {key}"></span>{word}</span>')


def legend(keys: list[str]) -> str:
    return '<ul class="legend">' + "".join(
        f'<li>{status(k)}</li>' for k in keys) + "</ul>"


def seconds(v: float | None) -> str:
    return "" if v is None else f"{v:.1f} s"


# ----------------------------------------------------------------- data

def load_trials() -> list[dict]:
    path = os.path.join(STUDY_DIR, "trials.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in open(path)]


@lru_cache(maxsize=1)
def corpus_titles() -> dict[str, str]:
    try:
        from corpus import load_corpus
        return {e.id: e.title for e in load_corpus()}
    except Exception:
        return {}


def cert_index(trials: list[dict]) -> dict[str, dict]:
    """certificate hash -> the trial that issued it"""
    index = {}
    for t in trials:
        for h in (t.get("detail") or {}).get("certificates", []):
            index[h] = t
    return index


def verification() -> dict:
    path = os.path.join(os.path.dirname(CERT_DIR), "certificate_verification.json")
    if not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path))
    except Exception:
        return {}
    data["by_sequence"] = {r["sequence"]: r
                           for r in data.get("replay", {}).get("rows", [])}
    return data


# ---------------------------------------------------------------- seal

def seal_svg(digest: str, size: int = 40) -> str:
    """A potter's seal drawn from the certificate hash: a mirrored 5x5
    stamp in a square frame. Same hash, same seal."""
    bits = bin(int(hashlib.sha256(digest.encode()).hexdigest(), 16))[2:].zfill(256)
    cells = []
    k = 0
    for row in range(5):
        for col in range(3):
            if bits[k] == "1":
                for c in {col, 4 - col}:
                    cells.append(f'<rect x="{6 + c * 5.6:.1f}" y="{6 + row * 5.6:.1f}" '
                                 f'width="5.6" height="5.6"/>')
            k += 1
    return (f'<svg class="seal" width="{size}" height="{size}" viewBox="0 0 40 40" '
            f'role="img" aria-label="Seal for certificate {E(digest[:8])}">'
            f'<rect x="1.5" y="1.5" width="37" height="37" rx="3" fill="none" '
            f'stroke="var(--seal)" stroke-width="2.4"/>'
            f'<g fill="var(--seal)">{"".join(cells)}</g></svg>')


# -------------------------------------------------------------- matrix

def matrix_svg(trials: list[dict], arms: list[str]) -> str:
    """Faults across, approaches down, one thin bar per seed in each cell.
    Faults are grouped by their pre-registered label, so the gated
    approach's declines can be read straight off against it."""
    titles = corpus_titles()
    entries = sorted({t["entry"] for t in trials})
    label = {}
    for t in trials:
        label[t["entry"]] = t.get("expected_c3", "healed")
    fix_group = [e for e in entries if label[e] != "refused"]
    refuse_group = [e for e in entries if label[e] == "refused"]
    seeds = sorted({t.get("seed", 1) for t in trials})
    cell = {}
    for t in trials:
        cell[(t["arm"], t["entry"], t.get("seed", 1))] = t["outcome"]

    left, top, pitch, w = 196, 64, 13, 10
    gap = 22
    bar_h, bar_gap = 5, 1.5
    row_h = len(seeds) * (bar_h + bar_gap) + 12
    xs = {}
    x = left
    for e in fix_group:
        xs[e] = x
        x += pitch
    x += gap
    for e in refuse_group:
        xs[e] = x
        x += pitch
    width = x + 4
    height = top + row_h * len(arms) + 4

    out = [f'<svg class="matrix" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" role="img" '
           f'aria-label="Outcome of every run, by fault and approach">'
           '<defs><pattern id="hatch" width="3" height="3" '
           'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
           '<rect width="3" height="3" fill="var(--slip)"/>'
           '<rect width="1.6" height="3" fill="var(--gaveup)"/></pattern></defs>']

    def bracket(group, text):
        if not group:
            return
        x0, x1 = xs[group[0]], xs[group[-1]] + w
        out.append(f'<text class="grp" x="{x0}" y="14">{E(text)}</text>')
        out.append(f'<path class="bracket" d="M{x0} 24 V20 H{x1} V24"/>')

    bracket(fix_group, f"Should be fixed ({len(fix_group)} faults)")
    bracket(refuse_group, f"Should be declined ({len(refuse_group)})")
    for e in entries:
        out.append(f'<text class="fid" transform="translate({xs[e] + 8},{top - 6}) '
                   f'rotate(-90)">{E(e)}</text>')

    for r, arm in enumerate(arms):
        y0 = top + r * row_h
        out.append(f'<text class="lab" x="0" y="{y0 + row_h / 2 - 2}">'
                   f'{E(ARM_NAME.get(arm, arm))}</text>')
        for e in entries:
            parts = []
            for i, s in enumerate(seeds):
                o = cell.get((arm, e, s))
                key = OUTCOME.get(o, ("none",))[0] if o else "none"
                parts.append(f'<rect class="c-{key}" x="{xs[e]}" '
                             f'y="{y0 + i * (bar_h + bar_gap):.1f}" width="{w}" '
                             f'height="{bar_h}" rx="1"/>')
            words = ", ".join(
                OUTCOME.get(cell.get((arm, e, s)), ("", "no run"))[1].lower()
                for s in seeds)
            tip = (f"{e}: {titles.get(e, '')}\n{ARM_NAME.get(arm, arm)}\n"
                   f"Runs: {words}")
            out.append("".join(parts))
            out.append(f'<rect class="hit" x="{xs[e] - 1.5}" y="{y0 - 1.5}" '
                       f'width="{w + 3}" height="{row_h - 9}" rx="2">'
                       f'<title>{E(tip)}</title></rect>')
    out.append("</svg>")
    return "".join(out)


# --------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def overview():
    all_trials = load_trials()
    if not all_trials:
        return page("Study", "overview",
                    "<h1>No study results yet</h1><p class=lede>Run the study "
                    "and its results will appear here, read straight from the "
                    "trial log.</p>")

    # the four-approach comparison is over base trials only; ablation
    # variants carry arm "C3" and would otherwise inflate the C3 figures
    trials = [t for t in all_trials if not t.get("variant")]
    ablations = [t for t in all_trials if t.get("variant")]
    arms = sorted({t["arm"] for t in trials})
    faults = sorted({t["entry"] for t in trials})
    seeds = sorted({t.get("seed", 1) for t in trials})

    def count(rows, outcome):
        return sum(1 for r in rows if r["outcome"] == outcome)

    summary = []
    for arm in arms:
        rows = [t for t in trials if t["arm"] == arm]
        mttrs = [r["mttr_seconds"] for r in rows if r["mttr_seconds"]]
        summary.append(
            f'<tr><td><b>{E(ARM_NAME.get(arm, arm))}</b>'
            f'<div class="quiet small">{E(ARM_BLURB.get(arm, ""))}</div></td>'
            f'<td class="num"><b>{count(rows, "recovered")}</b></td>'
            f'<td class="num">{count(rows, "refused")}</td>'
            f'<td class="num">{count(rows, "escalated")}</td>'
            f'<td class="num">{count(rows, "unrecovered")}</td>'
            f'<td class="num">{count(rows, "false_fix")}</td>'
            f'<td class="num">{seconds(statistics.median(mttrs)) if mttrs else ""}</td>'
            f'</tr>')

    gated = [t for t in trials if t["arm"] == "C3"]
    lede = ""
    if gated:
        should = [t for t in gated if t.get("expected_c3") == "refused"]
        right = sum(1 for t in should if t["outcome"] == "refused")
        wrong = sum(1 for t in gated if t["outcome"] == "refused"
                    and t.get("expected_c3") != "refused")
        shipped_bad = sum(count([t for t in trials if t["arm"] == a], "false_fix")
                          for a in arms)
        lede = (f"<p class=lede>The gated approach declined {right} of the "
                f"{len(should)} runs it was built to decline and "
                f"{'none' if not wrong else wrong} of the others. "
                + ("No approach shipped a bad fix in any run."
                   if not shipped_bad else
                   f"{shipped_bad} bad fixes were shipped in total.")
                + "</p>")

    ablation_html = ""
    if ablations:
        rows = []
        for variant in sorted({t["variant"] for t in ablations}):
            vs = [t for t in ablations if t["variant"] == variant]
            mttrs = [r["mttr_seconds"] for r in vs if r["mttr_seconds"]]
            rows.append(
                f'<tr><td>{E(VARIANT_NAME.get(variant, variant))}</td>'
                f'<td class="num">{count(vs, "recovered")}</td>'
                f'<td class="num">{count(vs, "refused")}</td>'
                f'<td class="num">{count(vs, "escalated")}</td>'
                f'<td class="num">{count(vs, "false_fix")}</td>'
                f'<td class="num">{seconds(statistics.median(mttrs)) if mttrs else ""}</td></tr>')
        ablation_html = (
            "<h2>Taking the gated approach apart</h2>"
            "<p>The gated approach run again with one part switched off at a "
            f"time, {len(ablations)} further runs. Removing the gate turns "
            "every decline into a fix attempt; removing retries is what hurts "
            "most, because the model's usual mistake is a patch that will not "
            "apply rather than a wrong one.</p>"
            '<div class="scroll"><table><tr><th>Variant</th>'
            '<th class="num">Fixed</th><th class="num">Declined</th>'
            '<th class="num">Gave up</th><th class="num">Bad fixes</th>'
            '<th class="num">Typical time to fix</th></tr>'
            f'{"".join(rows)}</table></div>')

    titles = corpus_titles()
    log = "".join(
        f'<tr><td>{E(t["entry"])}</td>'
        f'<td class="quiet">{E(titles.get(t["entry"], ""))}</td>'
        f'<td>{E(ARM_NAME.get(t["arm"], t["arm"]))}</td>'
        f'<td class="num">{t.get("seed", "")}</td>'
        f'<td>{status(t["outcome"])}</td>'
        f'<td class="num">{t["mttr_seconds"] if t["mttr_seconds"] is not None else ""}</td>'
        f'<td class="quiet small">{E(str((t.get("detail") or {}).get("heal_reason", ""))[:90])}</td></tr>'
        for t in trials)

    body = (
        f"<h1>{len(faults)} faults, four ways to recover from them</h1>"
        f"<p>Each fault was deployed into a live service under load and handed "
        f"to four recovery approaches, {len(seeds)} times each: "
        f"{len(trials)} runs. A run counts as fixed only when the failing "
        "request works again and the live traffic is clean.</p>"
        f"{lede}"
        '<div class="panel" style="margin-top:24px">'
        '<div class="scroll">' + matrix_svg(trials, arms) + "</div>"
        + legend(["recovered", "refused", "escalated", "unrecovered"])
        + '<p class="quiet small" style="margin:10px 0 0">One column per fault, '
        "one thin bar per run. Point at a column for the fault's name and "
        "what happened.</p></div>"
        "<h2>By approach</h2>"
        '<div class="scroll"><table><tr><th>Approach</th>'
        '<th class="num">Fixed</th><th class="num">Declined</th>'
        '<th class="num">Gave up</th><th class="num">Still broken</th>'
        '<th class="num">Bad fixes</th><th class="num">Typical time to fix</th>'
        f'</tr>{"".join(summary)}</table></div>'
        '<p class="quiet small">Typical time to fix is the median, measured '
        "from the first error to the moment the failing request works again.</p>"
        f"{ablation_html}"
        "<h2>Every run</h2>"
        f"<details><summary>Show all {len(trials)} runs</summary>"
        '<div class="scroll"><table><tr><th>Fault</th><th>What broke</th>'
        '<th>Approach</th><th class="num">Run</th><th>Result</th>'
        '<th class="num">Seconds to fix</th><th>Notes</th></tr>'
        f"{log}</table></div></details>")
    return page("Study", "overview", body)


@app.get("/certificates", response_class=HTMLResponse)
def certificates(page_no: int = 1):
    store = CertificateStore(CERT_DIR)
    try:
        chain = store.verify_chain()
        banner = (f'<div class="banner">{seal_svg("chain", 28)}<div><b>All '
                  f'{len(chain)} certificates check out.</b> <span class=quiet>'
                  "Each one matches its own fingerprint, links to the one "
                  "before it, and carries a valid signature.</span></div></div>")
    except CertificateError as exc:
        chain = store.all()
        banner = (f'<div class="banner"><span class="sw broken"></span><div>'
                  f'<b>A certificate failed its check.</b> {E(str(exc))}</div></div>')

    if not chain:
        return page("Certificates", "certificates",
                    "<h1>Repair certificates</h1>"
                    "<p class=lede>No certificates yet. Every fix the gated "
                    "approach ships is recorded here as a certificate.</p>")

    issued_by = cert_index(load_trials())
    titles = corpus_titles()
    pages = max(1, -(-len(chain) // PAGE_SIZE))
    page_no = min(max(1, page_no), pages)
    newest_first = list(reversed(chain))
    shown = newest_first[(page_no - 1) * PAGE_SIZE: page_no * PAGE_SIZE]

    items = []
    for c in shown:
        t = issued_by.get(c["hash"], {})
        entry = t.get("entry", "")
        context = []
        if entry:
            context.append(f"{entry}: {titles.get(entry, '')}".rstrip(": "))
        if t.get("variant"):
            context.append(VARIANT_NAME.get(t["variant"], t["variant"]))
        items.append(
            f'<li>{seal_svg(c["hash"])}<div class="what">'
            f'<a href="/certificates/{c["sequence"]}">Certificate '
            f'{c["sequence"]:06d}</a> <span class="quiet">'
            f'{E(c.get("signature") or "")}</span>'
            f'<div class="meta">{E("; ".join(context))}</div>'
            f'<div class="meta">Issued {E(c["issued_at"].replace("T", " at "))}, '
            f'fingerprint <span class="hash">{c["hash"][:16]}</span></div>'
            f'</div></li>')

    nav = ['<div class="pager">']
    if page_no > 1:
        nav.append(f'<a href="/certificates?page_no={page_no - 1}">Newer</a>')
    nav.append(f'<span class="quiet">Page {page_no} of {pages}</span>')
    if page_no < pages:
        nav.append(f'<a href="/certificates?page_no={page_no + 1}">Older</a>')
    nav.append("</div>")

    body = ("<h1>Repair certificates</h1>"
            "<p class=lede>Every fix the gated approach ships is written down "
            "as a certificate: the failure, the proof that it happened again "
            "on demand, every patch that was tried, the one that passed, and "
            "the checks it passed. Each certificate is chained to the one "
            "before it, like a seam, so altering any of them is detectable.</p>"
            f"{banner}<ol class=ledger>{''.join(items)}</ol>{''.join(nav)}")
    return page("Certificates", "certificates", body)


def diff_html(patch: str) -> str:
    lines = []
    for line in patch.splitlines():
        cls = ("d-hunk" if line.startswith("@@") else
               "" if line.startswith(("+++", "---")) else
               "d-add" if line.startswith("+") else
               "d-del" if line.startswith("-") else "")
        lines.append(f'<span class="{cls}">{E(line)}</span>' if cls else E(line))
    return "\n".join(lines)


COND_NAME = {"structural": "No cheating pattern",
             "applies": "Applies to the code",
             "reproduction": "Failure no longer happens",
             "suite": "Tests still pass"}


@app.get("/certificates/{seq}", response_class=HTMLResponse)
def certificate(seq: int):
    store = CertificateStore(CERT_DIR)
    certs = {c["sequence"]: c for c in store.all()}
    cert = certs.get(seq)
    if cert is None:
        return page("Certificate", "certificates",
                    "<h1>No such certificate</h1>"
                    '<p><a href="/certificates">Back to all certificates</a></p>')

    t = cert_index(load_trials()).get(cert["hash"], {})
    title = corpus_titles().get(t.get("entry", ""), "")
    capture = cert.get("capture") or {}
    req = capture.get("request") or {}
    repro = cert.get("reproduction") or {}
    verdicts = repro.get("verdicts") or []

    cands = []
    for cand in cert.get("candidates", []):
        conds = cand.get("conditions") or {}
        if isinstance(conds, str):
            conds = {}
        admitted = conds and all(conds.values())
        chips = "".join(
            f'<li class="{"pass" if ok else "fail"}">{COND_NAME.get(k, k)}</li>'
            for k, ok in conds.items())
        cands.append(
            f'<li><b>Attempt {cand.get("attempt")}</b>: '
            f'{"admitted" if admitted else "rejected"}'
            f'<ul class="conds">{chips}</ul></li>')

    ver = verification().get("by_sequence", {}).get(cert["sequence"])
    if ver:
        replay = ("<p><b>Replayed independently.</b> Starting from a fresh "
                  "copy of the faulty code and using only this certificate, "
                  + ("the failure happened again before the patch and stopped "
                     "after it." if ver.get("verified") else
                     f"the replay did not confirm the repair: {E(str(ver))}")
                  + "</p>")
    else:
        replay = ("<p class=quiet>Anyone holding this certificate can replay "
                  "the repair against the faulty code: the failure must happen "
                  "before the patch and stop after it.</p>")

    prev_seq = cert["sequence"] - 1 if cert["sequence"] > 0 else None
    next_seq = cert["sequence"] + 1 if cert["sequence"] + 1 in certs else None
    links = []
    if prev_seq is not None:
        links.append(f'<a href="/certificates/{prev_seq}">Previous certificate</a>')
    links.append('<a href="/certificates">All certificates</a>')
    if next_seq is not None:
        links.append(f'<a href="/certificates/{next_seq}">Next certificate</a>')

    payload = (cert.get("verification_record") or {}).get("payload", {})
    body = f"""
<div class="cert-head"><div>
<p class="quiet" style="margin:0">Repair certificate</p>
<h1 style="margin-top:4px">{cert['sequence']:06d}</h1>
<p class=lede>{E(title) if title else E(cert.get('signature') or '')}</p>
<p class="quiet small">Issued {E(cert['issued_at'].replace('T', ' at '))} for
<code>{E(cert.get('service_spec', ''))}</code>{', fault ' + E(t['entry']) if t.get('entry') else ''}</p>
</div>{seal_svg(cert['hash'], 104)}</div>

<ol class="steps">
<li><h3>The failure was captured</h3>
<p><code>{E(req.get('method', ''))} {E(req.get('path', ''))}</code> raised
<b>{E(capture.get('exception_type', ''))}</b>{': ' + E(str(capture.get('exception_message', ''))[:120]) if capture.get('exception_message') else ''}.
The platform named it <code>{E(cert.get('signature') or '')}</code>.</p></li>

<li><h3>It was made to happen again</h3>
<div class="replays" aria-hidden="true">{''.join('<span></span>' for _ in verdicts)}</div>
<p>{E(repro.get('reason', ''))}. Only a failure that can be repeated on
demand can be checked against a patch.</p></li>

<li><h3>Patches were tried</h3>
<ul style="padding-left:18px;margin:0">{''.join(cands)}</ul></li>

<li><h3>The admitted patch</h3>
<pre>{diff_html(cert.get('patch', ''))}</pre></li>

<li><h3>It was signed and chained</h3>
<p>The gate signed a record binding the failure to this exact patch
(fingerprint <span class="hash">{E(str(payload.get('diff_sha256', ''))[:16])}</span>),
and the certificate links to the one before it.</p>
<p class="quiet small">Previous <span class="hash">{cert['prev_hash'][:16]}</span>,
this <span class="hash">{cert['hash'][:16]}</span></p>
{replay}</li>
</ol>
<div class="pager">{''.join(links)}</div>"""
    return page(f"Certificate {seq:06d}", "certificates", body)


@app.get("/practice", response_class=HTMLResponse)
def practice():
    bank = PracticeBank(BANK_PATH)
    rows = bank.all()
    if not rows:
        return page("Practice", "practice",
                    "<h1>Practice</h1><p class=lede>Nothing rehearsed yet. "
                    "While the services are quiet, the platform breaks a "
                    "private copy of them on purpose and rehearses the repair. "
                    "Those rehearsals will appear here.</p>")
    counts = bank.counts()
    would = sum(1 for r in rows if r["status"] == "would_heal")
    refused = sum(1 for r in rows if r["status"] == "refused")
    tests = sum(1 for r in rows if r.get("regression_test"))
    word = {"would_heal": "recovered", "refused": "refused",
            "escalated": "escalated", "no_fire": "fault_never_fired"}
    titles = corpus_titles()
    table = "".join(
        f'<tr><td>{E(r["entry_id"])}</td>'
        f'<td class="quiet">{E(titles.get(r["entry_id"], ""))}</td>'
        f'<td>{status(word.get(r["status"], r["status"]))}</td>'
        f'<td class="quiet small">{E((r.get("rehearsed_at") or "").replace("T", " "))}</td>'
        f'<td class="quiet small">{E((r.get("reason") or "")[:80])}</td></tr>'
        for r in reversed(rows))
    body = ("<h1>Practice</h1>"
            "<p class=lede>While the services are quiet, the platform breaks a "
            "private copy of them on purpose and rehearses the repair, so it "
            "has seen a failure before it happens for real. Every rehearsal "
            "that ends in a fix also leaves a permanent test behind.</p>"
            '<div class="stats">'
            f'<div><span class="fig">{len(rows)}</span><span class="quiet">rehearsals</span></div>'
            f'<div><span class="fig">{len(counts)}</span><span class="quiet">faults covered</span></div>'
            f'<div><span class="fig">{would}</span><span class="quiet">would be fixed</span></div>'
            f'<div><span class="fig">{refused}</span><span class="quiet">would be declined</span></div>'
            f'<div><span class="fig">{tests}</span><span class="quiet">tests banked</span></div>'
            "</div>"
            '<div class="scroll"><table><tr><th>Fault</th><th>What broke</th>'
            "<th>Rehearsal</th><th>When</th><th>Notes</th></tr>"
            f"{table}</table></div>")
    return page("Practice", "practice", body)


@app.get("/incidents", response_class=HTMLResponse)
def incidents():
    try:
        from control_plane.api.main import list_incidents
        rows = list_incidents()
    except Exception as exc:
        return page("Incidents", "incidents",
                    "<h1>Incidents</h1><p class=lede>The incident store could "
                    f"not be read: {E(str(exc))}. Check the database "
                    "connection.</p>")
    if not rows:
        body = ("<h1>Incidents</h1><p class=lede>No failures have been "
                "reported by the live services. When one fails, its captures "
                "are grouped into an incident here.</p>")
        return page("Incidents", "incidents", body)
    table = "".join(
        f'<tr><td class="num">{r["id"]}</td><td>{E(r["service"])}</td>'
        f'<td>{E(r["exception_type"])}</td><td>{E(r["release"])}</td>'
        f'<td class="num">{r["capture_count"]}</td><td>{E(r["status"])}</td>'
        f'<td class="quiet small">{E(str(r["created_at"]).replace("T", " ")[:19])}</td></tr>'
        for r in rows)
    body = ("<h1>Incidents</h1>"
            "<p class=lede>Failures reported by the live services. Repeats of "
            "the same failure are grouped into one incident, so a burst of "
            "errors is one piece of work, not hundreds.</p>"
            '<div class="scroll"><table><tr><th class="num">Number</th>'
            "<th>Service</th><th>Error</th><th>Release</th>"
            '<th class="num">Times seen</th><th>Status</th><th>First seen</th>'
            f"</tr>{table}</table></div>"
            '<p class="quiet small">Raw data: <a href="/api/incidents">/api/incidents</a></p>')
    return page("Incidents", "incidents", body)
