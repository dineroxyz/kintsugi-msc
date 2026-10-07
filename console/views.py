"""HTML components for the Kintsugi console.

Small functions that turn data from console.data into markup. They never
compute experiment numbers themselves; they only format what data.py gives
them. Everything is escaped on the way out.
"""

from __future__ import annotations

import hashlib
import html
from urllib.parse import urlencode

from console import data as D

E = html.escape


# ---------------------------------------------------------------- format

def fmt_s(v: float | None, missing: str = "n/a") -> str:
    return missing if v is None else f"{v:.1f} s"


def pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "n/a"


def qs(params: dict) -> str:
    return ("?" + urlencode(params)) if params else ""


# ----------------------------------------------------------------- brand

def seam(cls: str = "seam") -> str:
    """The brand line: a level seam with three deliberate breaks, each
    mended and returning to the same level."""
    return (
        f'<svg class="{cls}" viewBox="0 0 1000 18" preserveAspectRatio="none" '
        'aria-hidden="true" focusable="false">'
        '<path pathLength="1" d="M0 9 H212 l9 -4 l7 7 l8 -5 l6 2 H486 '
        'l6 5 l8 -8 l7 5 l5 -2 H742 l7 3 l6 -6 l9 3 H1000"/>'
        '</svg>')


THEME_BOOT = """<script>
(function(){var t=null;try{t=new URLSearchParams(location.search).get("theme")||
localStorage.getItem("kintsugi-theme");}catch(e){}
if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t);})();
</script>"""

ICON_SUN = ('<svg class="sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" '
            'cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 '
            '1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>')
ICON_MOON = ('<svg class="moon" viewBox="0 0 24 24" aria-hidden="true"><path '
             'd="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>')
ICON_CLOSE = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 '
              '6L6 18"/></svg>')

NAV = [("/live", "live", "Live"),
       ("/results", "results", "Results"),
       ("/certificates", "certificates", "Certificates"),
       ("/practice", "practice", "Practice"),
       ("/guide", "guide", "Guide")]


def shell(title: str, active: str, body: str, *, description: str = "",
          user: str | None = None, csrf: str = "", wide: bool = False,
          extra_head: str = "") -> str:
    links = list(NAV)
    if user:
        links.append(("/incidents", "errors", "Errors"))
    nav = "".join(
        f'<a href="{href}"{" aria-current=page" if key == active else ""}>'
        f"{label}</a>" for href, key, label in links)
    if user:
        account = (f'<form class="signout" method="post" action="/logout">'
                   f'<input type="hidden" name="csrf" value="{E(csrf)}">'
                   f'<button class="nav-btn" type="submit" title="Signed in as {E(user)}">'
                   f'Sign out</button></form>')
    else:
        account = (f'<a class="nav-btn" href="/login"'
                   f'{" aria-current=page" if active == "login" else ""}>Sign in</a>')
    desc = description or ("Kintsugi is a self-healing software platform: it "
                           "repairs live apps with AI, but only with proof.")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{E(title)} | Kintsugi</title>
<meta name="description" content="{E(desc)}">
<meta name="color-scheme" content="dark light">
<meta name="referrer" content="strict-origin-when-cross-origin">
{THEME_BOOT}
<link rel="preload" href="/static/fonts/shippori-mincho-latin-700-normal.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/static/fonts/zen-kaku-gothic-new-latin-400-normal.woff2" as="font" type="font/woff2" crossorigin>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/console.css">
<script src="/static/console.js" defer></script>
{extra_head}
</head><body{' class="wide"' if wide else ""}>
<a class="skip" href="#main">Skip to content</a>
<header class="site-head"><div class="wrap">
<div class="bar"><a class="mark" href="/" aria-label="Kintsugi, home">Kintsugi</a>
<nav class="nav" aria-label="Main">{nav}
<button class="theme" type="button" data-theme-toggle aria-label="Switch theme">{ICON_SUN}{ICON_MOON}<span class="theme-text">Theme</span></button>
{account}</nav></div>{seam()}</div></header>
<main id="main"{' class="wide"' if wide else ""}><div class="wrap">{body}</div></main>
<footer class="foot"><div class="wrap">{seam()}
<div class="foot-grid">
<div class="foot-brand"><p class="byline"><b>Kintsugi</b></p>
<p class="small muted">A self-healing software platform that repairs live
apps with AI, but only with proof. MSc Computer Science project, University
of Lagos.</p>
<p class="small muted">By Abe Oluwaseyi Elizabeth.
<a href="mailto:oluwaseyiabe0@gmail.com">oluwaseyiabe0@gmail.com</a></p></div>
<nav aria-label="Explore"><h2 class="foot-h">Explore</h2>
<a href="/results">Results</a><a href="/certificates">Certificates</a>
<a href="/practice">Practice</a></nav>
<nav aria-label="Help"><h2 class="foot-h">Help</h2>
<a href="/guide">How to use this site</a><a href="/guide#glossary">Glossary</a>
<a href="/guide#verify">Check a certificate yourself</a></nav>
<nav aria-label="Data and policy"><h2 class="foot-h">Data and policy</h2>
<a href="/data/trials.json">Raw test data</a>
<a href="/data/verification.json">Certificate check report</a>
<a href="/privacy">Privacy policy</a></nav>
</div>
<p class="foot-note small faint">Fonts are served from this site. No tracking, no
analytics, no third-party requests.</p>
</div></footer>
<dialog class="detail" id="detail" aria-labelledby="detail-title">
<div class="detail-head"><span class="detail-title muted" id="detail-title">Details</span>
<button class="close" type="button" aria-label="Close">{ICON_CLOSE}</button></div>
<div class="detail-body"></div></dialog>
</body></html>"""


# --------------------------------------------------------------- statuses

def status(outcome: str) -> str:
    key = D.classify(outcome)
    label = D.OUTCOME_LABEL.get(outcome, outcome or "Unknown")
    return (f'<span class="status"><span class="sw k-{key}" aria-hidden="true">'
            f"</span>{E(label)}</span>")


def legend(outcomes: list[str]) -> str:
    return ('<ul class="legend" aria-label="Key">' +
            "".join(f"<li>{status(o)}</li>" for o in outcomes) + "</ul>")


def mix_bar(s: D.Summary, outcomes: list[str]) -> str:
    """A stacked bar of outcome shares; text alternative in aria-label."""
    parts, words = [], []
    for o in outcomes:
        c = s.count(o)
        if not c:
            continue
        parts.append(f'<i class="k-{D.classify(o)}" style="flex:{c}"></i>')
        words.append(f"{D.OUTCOME_LABEL[o].lower()} {c}")
    label = f"Out of {s.n}: " + ", ".join(words) if words else "No tests"
    return f'<div class="mix" role="img" aria-label="{E(label)}">{"".join(parts)}</div>'


MAIN_OUTCOMES = ["recovered", "refused", "escalated", "unrecovered"]


# ------------------------------------------------------------------- hero

def hero(trials: list[dict], check: dict, origins: dict | None = None) -> str:
    leave = D.correct_leave_alone(trials)
    summaries = D.by_approach(trials)
    bad = sum(s.count("false_fix") for s in summaries.values())
    held = ("never held back a fix it could have made" if not leave["wrongly"]
            else f"held back a fix it could have made {leave['wrongly']} times")
    shipped = ("no approach ever put a bad fix live" if not bad
               else f"{bad} bad fixes went live across all approaches")
    finding = (f"In these tests the safety check left alone {leave['right']} of "
               f"the {leave['should']} tests on bugs it could not safely test, "
               f"{held}, and {shipped}.")
    if check["ok"]:
        integrity = (
            '<p class="integrity"><span class="tick" aria-hidden="true">✓</span>'
            f'<span>Data check passed: {check["tests"]} test records, '
            f'{check["bugs"]} bugs × {check["approaches"]} approaches × '
            f'{check["runs"]} runs, with no gaps or duplicates. Every number on '
            "this page is counted from those records.</span></p>")
    else:
        items = "".join(f"<li>{E(p)}</li>" for p in check["problems"])
        integrity = (
            '<div class="integrity bad" role="alert"><div><b>The test records '
            "do not add up.</b> The numbers below are shown exactly as "
            f"recorded, but check these first:<ul>{items}</ul></div></div>")
    origin_note = ""
    if origins and origins.get("mined"):
        origin_note = (f" Of the {check['bugs']} bugs, {origins['mined']} were taken "
                       "from real bug fixes in open-source projects and "
                       f"{origins.get('seeded', 0)} were written for the study.")
    return f"""
<section class="hero" aria-labelledby="hero-title">
<h1 id="hero-title">60 bugs, four ways to deal with them</h1>
<p class="lede">When a live app crashes, someone has to decide what to do.
This experiment planted software bugs into a running app, one at a time, and
compared four responses: doing nothing, restarting and rolling back, letting an
AI write a fix, and letting an AI write a fix that has to pass a safety check
first.{origin_note}</p>
<p class="muted">A bug only counts as fixed when the app works again and no
new errors appear. {E(finding)}</p>
<dl class="facts">
<div><dt>unique bugs</dt><dd>{check["bugs"]}</dd></div>
<div><dt>tests in total</dt><dd>{check["tests"]}</dd></div>
<div><dt>approaches compared</dt><dd>{check["approaches"]}</dd></div>
<div><dt>runs per bug, per approach</dt><dd>{check["runs"]}</dd></div>
</dl>
{integrity}
</section>"""


# ----------------------------------------------------------------- matrix

def _runs_text(rows: list[dict]) -> str:
    out = []
    for t in rows:
        label = D.OUTCOME_LABEL.get(t["outcome"], t["outcome"])
        if t["outcome"] == "recovered" and t.get("mttr_seconds") is not None:
            label += f" in {t['mttr_seconds']:.1f} s"
        out.append(label)
    return "|".join(out)


def matrix(trials: list[dict], titles: dict[str, str]) -> str:
    fix, alone = D.bug_groups(trials)
    cells = D.cell_index(trials)
    summaries = D.by_approach(trials)
    runs = sorted({t.get("seed") for t in D.main_trials(trials)})
    expected = D.expected_of(D.main_trials(trials))

    def ids(group):
        return "".join(f"<span>{E(b)}</span>" for b in group)

    head = (f'<div class="mx-head" aria-hidden="true"><div></div>'
            f'<div><div class="mx-grouphead">Bugs that can be fixed ({len(fix)})</div>'
            f'<div class="mx-ids">{ids(fix)}</div></div>'
            f'<div><div class="mx-grouphead">Bugs to leave alone ({len(alone)})</div>'
            f'<div class="mx-ids">{ids(alone)}</div></div></div>')

    body = []
    for a in D.APPROACHES:
        if a not in summaries:
            continue
        s = summaries[a]
        dist = "".join(
            f'<i class="k-{D.classify(o)}" style="flex:{s.count(o)}"></i>'
            for o in MAIN_OUTCOMES if s.count(o))
        counts = ", ".join(f"{D.OUTCOME_LABEL[o].lower()} {s.count(o)}"
                           for o in MAIN_OUTCOMES if s.count(o))

        def group(bugs, label):
            out = []
            for b in bugs:
                rs = cells.get((a, b), [])
                by_run = {t.get("seed"): t for t in rs}
                bars = "".join(
                    f'<i class="k-{D.classify(by_run[r]["outcome"]) if r in by_run else "empty"}"></i>'
                    for r in runs)
                runs_text = _runs_text(rs) or "no test recorded"
                aria = (f"{b}, {titles.get(b, '')}. {D.APPROACH_NAME[a]}. "
                        f"{D.EXPECTED_LABEL.get(expected.get(b), '')}. "
                        + ". ".join(f"Run {i + 1}: {x}" for i, x in
                                    enumerate(runs_text.split("|"))))
                out.append(
                    f'<span class="mx-gc" role="gridcell"><a class="mx-cell" href="/bugs/{E(b)}" '
                    f'data-detail="/fragment/bug/{E(b)}" data-label="Bug {E(b)}" '
                    f'data-bug="{E(b)}" data-title="{E(titles.get(b, ""))}" '
                    f'data-approach="{E(D.APPROACH_NAME[a])}" '
                    f'data-expected="{E(D.EXPECTED_LABEL.get(expected.get(b), ""))}" '
                    f'data-runs="{E(runs_text)}" aria-label="{E(aria)}">{bars}</a></span>')
            return (f'<div class="mx-group" role="presentation" data-label="{E(label)}">'
                    f'{"".join(out)}</div>')

        body.append(
            f'<div class="mx-row" role="row">'
            f'<div class="mx-label" role="rowheader"><b>{E(D.APPROACH_NAME[a])}</b>'
            f'<span>{E(counts)}</span>'
            f'<div class="mx-dist" aria-hidden="true">{dist}</div></div>'
            f'<div class="mx-groups" role="presentation">'
            f'{group(fix, f"Bugs that can be fixed ({len(fix)})")}'
            f'{group(alone, f"Bugs to leave alone ({len(alone)})")}</div></div>')

    return (f'<div class="mx" role="grid" aria-label="Outcome of every test: one '
            f'cell per bug and approach, one bar per run. Use the arrow keys to '
            f'move and Enter to open a bug." aria-describedby="mx-help">'
            f'{head}{"".join(body)}</div>'
            f'<p class="mx-foot" id="mx-help">Each cell is one bug; its '
            f'{len(runs)} thin bars are its {len(runs)} runs. Hover or focus a '
            'cell for the details, and select it to see that bug\'s full test '
            'history. On a keyboard, use the arrow keys to move around the '
            'grid.</p>')


def matrix_table(trials: list[dict], titles: dict[str, str]) -> str:
    """The same data as the grid, as a plain table: for screen readers,
    printing, and anyone who prefers words to colours."""
    fix, alone = D.bug_groups(trials)
    cells = D.cell_index(trials)
    expected = D.expected_of(D.main_trials(trials))
    heads = "".join(f'<th scope="col">{E(D.APPROACH_NAME[a])}</th>'
                    for a in D.APPROACHES)
    rows = []
    for b in fix + alone:
        tds = "".join(
            "<td>" + E(", ".join(D.OUTCOME_LABEL.get(t["outcome"], t["outcome"])
                                 for t in cells.get((a, b), [])) or "n/a") + "</td>"
            for a in D.APPROACHES)
        rows.append(f'<tr><th scope="row"><a href="/bugs/{E(b)}" '
                    f'data-detail="/fragment/bug/{E(b)}" data-label="Bug {E(b)}">'
                    f'{E(b)}</a> <span class="faint small">{E(titles.get(b, ""))}</span></th>'
                    f'<td class="small">{E(D.EXPECTED_LABEL.get(expected.get(b), ""))}</td>'
                    f"{tds}</tr>")
    return (f'<details class="more"><summary>Show the grid as a table</summary>'
            f'<div class="table-scroll"><table class="ex-table">'
            f'<caption>Results of each run, in run order, for every bug and approach.</caption>'
            f'<thead><tr><th scope="col">Bug</th><th scope="col">Expected</th>{heads}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></details>')


# --------------------------------------------------------------- compare

def compare_table(trials: list[dict]) -> str:
    summaries = D.by_approach(trials)
    rows = []
    for a, s in summaries.items():
        def cell(o):
            c = s.count(o)
            return (f'<td class="num">{c}<span class="pct">{pct(c, s.n)}</span></td>')
        bad = (cell("false_fix") if a in D.DEPLOYS_FIXES else
               '<td class="num"><span class="na" title="This approach never puts '
               'a code fix live, so a bad fix is not possible">n/a</span></td>')
        time = (f'<td class="num">{fmt_s(s.time)}</td>' if s.time is not None else
                '<td class="num"><span class="na">none fixed</span></td>')
        rows.append(
            f'<tr><td class="appr"><a href="/results?approach={a}#tests" '
            f'aria-label="{E(D.APPROACH_NAME[a])}: show its {s.n} tests">'
            f'{E(D.APPROACH_NAME[a])}</a><p>{E(D.APPROACH_BLURB[a])}</p></td>'
            f'{cell("recovered")}{cell("refused")}{cell("escalated")}'
            f'{cell("unrecovered")}{bad}{time}</tr>')
    n = next(iter(summaries.values())).n if summaries else 0
    return f"""
<div class="table-scroll"><table class="compare">
<caption>Each approach was tested {n} times: every bug, three runs each.
Percentages are out of {n}. Select an approach to see its tests.</caption>
<thead><tr><th scope="col">Approach</th>
<th scope="col" class="num">Fixed</th><th scope="col" class="num">Left alone</th>
<th scope="col" class="num">Gave up</th><th scope="col" class="num">Still broken</th>
<th scope="col" class="num">Bad fixes</th><th scope="col" class="num">Usual time to fix</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<details class="more"><summary>What the columns mean</summary>
<dl class="defs">
<dt>Fixed</dt><dd>{E(D.OUTCOME_MEANING["recovered"])}</dd>
<dt>Left alone</dt><dd>{E(D.OUTCOME_MEANING["refused"])} Only the approach with
the safety check can do this.</dd>
<dt>Gave up</dt><dd>{E(D.OUTCOME_MEANING["escalated"])}</dd>
<dt>Still broken</dt><dd>{E(D.OUTCOME_MEANING["unrecovered"])}</dd>
<dt>Bad fixes</dt><dd>{E(D.OUTCOME_MEANING["false_fix"])} Shown as n/a for the
two approaches that never put a code fix live, because zero would wrongly
suggest they had been tested for it.</dd>
<dt>Usual time to fix</dt><dd>The middle value (median) of the seconds from the
first error to the moment the app works again, counted over fixed tests only.
Shown as "none fixed" when an approach fixed nothing.</dd>
</dl></details>"""


# --------------------------------------------------------------- ablation

def ablation(trials: list[dict]) -> str:
    variants = D.by_variant(trials)
    if not variants:
        return ""
    base = D.by_approach(trials).get("C3")
    outcomes = ["recovered", "refused", "escalated", "unrecovered", "false_fix"]

    def row(name, change, s, cls=""):
        return (f'<tr class="{cls}"><td class="cfg"><b>{E(name)}</b>'
                f'<span>{E(change)}</span></td>'
                f'<td>{mix_bar(s, outcomes)}</td>'
                f'<td class="num">{s.count("recovered")}</td>'
                f'<td class="num">{s.count("refused")}</td>'
                f'<td class="num">{s.count("escalated")}</td>'
                f'<td class="num">{s.count("false_fix")}</td>'
                f'<td class="num">{fmt_s(s.time, "none fixed")}</td></tr>')

    rows = []
    if base:
        rows.append(row("Normal setup", "The full safety check, as in the main "
                        "results above.", base, "baseline"))
    for v, s in variants.items():
        rows.append(row(D.VARIANT_NAME.get(v, v), D.VARIANT_CHANGE.get(v, ""), s))
    n = next(iter(variants.values())).n
    noregen = variants.get("C3-noregen")
    noguard = variants.get("C3-noguard")
    repeat = variants.get("C3-nomemory")
    measured = []
    if noguard and base:
        measured.append(
            f"Without the safety check, {noguard.count('recovered')} of {n} tests "
            f"ended fixed, against {base.count('recovered')} with it, and no bugs "
            "were left alone.")
    if noregen and base:
        measured.append(
            f"With only one try, {noregen.count('recovered')} of {n} tests ended "
            f"fixed and {noregen.count('escalated')} gave up, against "
            f"{base.count('recovered')} and {base.count('escalated')} normally.")
    if repeat and base:
        measured.append(
            f"Repeating the normal setup gave {repeat.count('recovered')} fixed and "
            f"{repeat.count('refused')} left alone, against "
            f"{base.count('recovered')} and {base.count('refused')} the first time.")
    if all(s.count("false_fix") == 0 for s in variants.values()):
        measured.append("No version put a bad fix live.")
    return f"""
<section class="section" aria-labelledby="ablation-title">
<div class="section-head"><h2 id="ablation-title">Which parts of Kintsugi matter</h2>
<p>The approach with the safety check was tested again with one part switched
off at a time: {len(variants) * n} more tests, {n} for each version, on the
same {n // 3 if n else 0} bugs. These tests are separate from the
{sum(s.n for s in D.by_approach(trials).values())} in the main results.</p></div>
<div class="table-scroll"><table class="ablation">
<thead><tr><th scope="col">Version</th><th scope="col">Outcome mix</th>
<th scope="col" class="num">Fixed</th><th scope="col" class="num">Left alone</th>
<th scope="col" class="num">Gave up</th><th scope="col" class="num">Bad fixes</th>
<th scope="col" class="num">Usual time to fix</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
{legend(["recovered", "refused", "escalated"])}
<div class="readings">
<div><h3>What was measured</h3><ul>{"".join(f"<li>{E(m)}</li>" for m in measured)}</ul></div>
<div><h3>What it suggests</h3><ul>
<li>The safety check is what makes Kintsugi leave some bugs alone. Without it,
those bugs got fixes too, and on these bugs none of those fixes went wrong, so
the check's value here is the proof behind each fix rather than fewer bad
fixes.</li>
<li>Letting the AI try again matters most. Looking at the rejected attempts,
the AI's usual mistake was a fix written in the wrong format rather than the
wrong fix, and a second try corrects that.</li>
<li>The repeat run came out almost the same, which suggests the results are
stable from one run to the next. These are readings of one experiment on one
app, not general rules.</li>
</ul></div></div>
</section>"""


# --------------------------------------------------------------- explorer

def _select(name: str, label: str, options: list[tuple[str, str]], value: str) -> str:
    opts = "".join(f'<option value="{E(v)}"{" selected" if v == value else ""}>'
                   f"{E(t)}</option>" for v, t in options)
    return (f'<div class="field"><label for="f-{name}">{E(label)}</label>'
            f'<select id="f-{name}" name="{name}">{opts}</select></div>')


def explorer(trials: list[dict], q: D.Query, titles: dict[str, str]) -> str:
    runs = sorted({str(t.get("seed")) for t in D.main_trials(trials)})
    outcomes_present = [o for o in D.OUTCOMES
                        if any(t["outcome"] == o for t in D.main_trials(trials))]
    total = len(D.main_trials(trials))
    form = (
        f'<form id="ex-form" class="filters" method="get" action="/results#tests" role="search" '
        f'aria-label="Filter tests">'
        f'<div class="field"><label for="f-bug">Bug ID</label>'
        f'<input id="f-bug" type="search" name="bug" value="{E(q.bug)}" '
        f'placeholder="For example F03" autocomplete="off" spellcheck="false"></div>'
        + _select("approach", "Approach",
                  [("", "All approaches")] + [(a, D.APPROACH_NAME[a]) for a in D.APPROACHES],
                  q.approach)
        + _select("outcome", "Result",
                  [("", "All results")] + [(o, D.OUTCOME_LABEL[o]) for o in outcomes_present],
                  q.outcome)
        + _select("run", "Run", [("", "All runs")] + [(r, f"Run {r}") for r in runs], q.run)
        + f'<input type="hidden" name="sort" value="{E(q.sort)}">'
          f'<input type="hidden" name="order" value="{E(q.order)}">'
          '<div class="field"><button class="btn" type="submit">Show tests</button></div>'
          "</form>")
    return f"""
<section class="section" id="tests" aria-labelledby="tests-title">
<div class="section-head"><h2 id="tests-title">Every test</h2>
<p>All {total} test records, exactly as logged. Filter them, sort them, and
open any one for its full timeline. The address bar keeps your filters, so a
filtered view can be shared as a link.</p></div>
{form}
<div id="ex-results" class="ex-results" aria-live="polite">{explorer_results(trials, q, titles)}</div>
</section>"""


def explorer_results(trials: list[dict], q: D.Query, titles: dict[str, str]) -> str:
    res = D.explore(trials, q)
    if not res["total"]:
        return ('<div class="empty"><p><b>No tests match these filters.</b></p>'
                '<p>Try a shorter bug ID, or clear the filters to see all tests.</p>'
                '<a class="btn" href="/results#tests" data-ex-reset>Clear filters</a></div>')

    def sort_link(key: str, label: str, numeric: bool = False) -> str:
        active = q.sort == key
        order = "desc" if active and q.order == "asc" else "asc"
        arrow = ("↑" if q.order == "asc" else "↓") if active else ""
        aria = (f' aria-sort="{"ascending" if q.order == "asc" else "descending"}"'
                if active else "")
        href = "/results" + qs(q.params(sort=key, order=order, page=1)) + "#tests"
        return (f'<th scope="col"{aria}{" class=num" if numeric else ""}>'
                f'<a href="{E(href)}" data-ex>{label}'
                f'<span class="arrow" aria-hidden="true">{arrow}</span>'
                f'<span class="sr-only">, sort</span></a></th>')

    rows = []
    for t in res["rows"]:
        label = f"Bug {t['entry']}, {D.APPROACH_NAME[t['arm']]}, run {t.get('seed')}"
        rows.append(
            f'<tr><td><a class="mono" href="/tests/{t["id"]}" '
            f'data-detail="/fragment/test/{t["id"]}" data-label="{E(label)}" '
            f'aria-label="Open test: {E(label)}">{E(t["entry"])}</a>'
            f'<div class="what">{E(titles.get(t["entry"], ""))}</div></td>'
            f'<td data-label="Approach">{E(D.APPROACH_NAME.get(t["arm"], t["arm"]))}</td>'
            f'<td class="num" data-label="Run">{E(str(t.get("seed", "")))}</td>'
            f'<td data-label="Result">{status(t["outcome"])}</td>'
            f'<td class="num" data-label="Time to fix">{fmt_s(t.get("mttr_seconds"), "n/a")}</td>'
            f'<td class="why" data-label="Note">{E(D.plain_reason((t.get("detail") or {}).get("heal_reason", "")))}</td></tr>')

    pager = ['<nav class="pager" aria-label="Pages">']
    if res["page"] > 1:
        href = "/results" + qs(q.params(page=res["page"] - 1)) + "#tests"
        pager.append(f'<a class="btn" href="{E(href)}" data-ex data-ex-top>Previous</a>')
    else:
        pager.append('<span class="btn" aria-disabled="true">Previous</span>')
    pager.append(f'<span class="muted small">Page {res["page"]} of {res["pages"]}</span>')
    if res["page"] < res["pages"]:
        href = "/results" + qs(q.params(page=res["page"] + 1)) + "#tests"
        pager.append(f'<a class="btn" href="{E(href)}" data-ex data-ex-top>Next</a>')
    else:
        pager.append('<span class="btn" aria-disabled="true">Next</span>')
    pager.append("</nav>")

    reset = ('<a class="btn btn-quiet" href="/results#tests" data-ex-reset>Clear filters</a>'
             if q.active() else "")
    return f"""
<div class="ex-status"><span>Showing {res["start"]} to {res["end"]} of {res["total"]} tests</span>{reset}</div>
<div class="table-scroll"><table class="ex-table ex-cards">
<thead><tr>{sort_link("bug", "Bug")}{sort_link("approach", "Approach")}
<th scope="col" class="num">Run</th>{sort_link("result", "Result")}
{sort_link("time", "Time to fix", True)}<th scope="col">Notes</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
{"".join(pager)}"""


# ---------------------------------------------------------------- details

def bug_detail(trials: list[dict], bug: str, titles: dict[str, str]) -> str | None:
    rows = [t for t in trials if t["entry"] == bug]
    if not rows:
        return None
    expected = D.expected_of(rows).get(bug, "")
    main = D.main_trials(rows)
    abl = D.ablation_trials(rows)

    def table(rs, by, rank):
        trs = "".join(
            f'<tr><td>{E(by(t))}</td><td class="num">{t.get("seed")}</td>'
            f'<td>{status(t["outcome"])}</td>'
            f'<td class="num">{fmt_s(t.get("mttr_seconds"))}</td>'
            f'<td><a href="/tests/{t["id"]}">Open</a></td></tr>'
            for t in sorted(rs, key=lambda t: (rank(t), t.get("seed", 0))))
        return ('<div class="table-scroll"><table class="ex-table" style="min-width:560px">'
                '<thead><tr><th scope="col">Approach</th><th scope="col" class="num">Run</th>'
                '<th scope="col">Result</th><th scope="col" class="num">Time to fix</th>'
                f'<th scope="col"><span class="sr-only">Link</span></th></tr></thead><tbody>{trs}</tbody></table></div>')

    a_rank = {a: i for i, a in enumerate(D.APPROACHES)}
    v_rank = {v: i for i, v in enumerate(D.VARIANTS)}
    main_t = table(main, lambda t: D.APPROACH_NAME.get(t["arm"], t["arm"]),
                   lambda t: a_rank.get(t["arm"], 99))
    abl_t = (f'<h3 style="margin-top:32px;font-size:18px">Extra tests with parts '
             f'switched off</h3>'
             f'{table(abl, lambda t: D.VARIANT_NAME.get(t["variant"], t["variant"]), lambda t: v_rank.get(t["variant"], 99))}'
             if abl else "")
    return (f'<h2>Bug {E(bug)}</h2><p class="lede">{E(titles.get(bug, ""))}</p>'
            f'<p class="muted">{E(D.EXPECTED_LABEL.get(expected, ""))}. '
            f'{len(main)} tests in the main results.</p>{main_t}{abl_t}')


def test_detail(t: dict, titles: dict[str, str], cert_seq: dict[str, int]) -> str:
    tm = t.get("timings") or {}
    start = tm.get("fault_deployed")
    steps = [("fault_deployed", "The bug went live"),
             ("first_error", "First error seen"),
             ("heal_finished", "A fix passed its checks"),
             ("patch_deployed", "The fix went live"),
             ("recovered", "The app worked again")]
    tl = "".join(
        f'<li><span class="t num">{tm[k] - start:+.1f} s</span><span>{E(label)}</span></li>'
        for k, label in steps if start is not None and isinstance(tm.get(k), (int, float)))
    detail = t.get("detail") or {}
    tries = detail.get("candidates")
    certs = [h for h in detail.get("certificates", []) if h in cert_seq]
    cert_links = ", ".join(f'<a href="/certificates/{cert_seq[h]}">certificate '
                           f'{cert_seq[h]:06d}</a>' for h in certs)
    rows = [("Bug", f'<a href="/bugs/{E(t["entry"])}">{E(t["entry"])}</a> '
                    f'{E(titles.get(t["entry"], ""))}'),
            ("Expected", E(D.EXPECTED_LABEL.get(t.get("expected_c3"), "n/a"))),
            ("Approach", E(D.APPROACH_NAME.get(t["arm"], t["arm"]))
             + (f' ({E(D.VARIANT_NAME.get(t["variant"], t["variant"]))})'
                if t.get("variant") else "")),
            ("Run", E(str(t.get("seed", "")))),
            ("Result", status(t["outcome"])),
            ("What that means", E(D.OUTCOME_MEANING.get(t["outcome"], ""))),
            ("Time to fix", fmt_s(t.get("mttr_seconds"))),
            ("AI fixes tried", E(str(tries)) if tries is not None else "n/a"),
            ("Note", E(D.plain_reason(detail.get("heal_reason", ""))) or "n/a")]
    if cert_links:
        rows.append(("Proof", cert_links))
    kv = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
    return (f'<h2>Test {t["id"]}</h2><dl class="kv">{kv}</dl>'
            + (f'<h3 style="font-size:18px">Timeline</h3><ol class="timeline">{tl}</ol>'
               if tl else ""))


# ------------------------------------------------------------ certificate

def seal(digest: str, size: int = 40) -> str:
    """A potter's seal drawn from the certificate hash: same hash, same seal."""
    bits = bin(int(hashlib.sha256(digest.encode()).hexdigest(), 16))[2:].zfill(256)
    cells, k = [], 0
    for row in range(5):
        for col in range(3):
            if bits[k] == "1":
                for c in {col, 4 - col}:
                    cells.append(f'<rect x="{6 + c * 5.6:.1f}" y="{6 + row * 5.6:.1f}" '
                                 'width="5.6" height="5.6"/>')
            k += 1
    return (f'<svg class="seal" width="{size}" height="{size}" viewBox="0 0 40 40" '
            f'aria-hidden="true"><rect x="1.5" y="1.5" width="37" height="37" '
            'rx="3" fill="none" stroke="var(--broken)" stroke-width="2.4"/>'
            f'<g fill="var(--broken)">{"".join(cells)}</g></svg>')


def diff_html(patch: str) -> str:
    out = []
    for line in (patch or "").splitlines():
        cls = ("d-hunk" if line.startswith("@@") else
               "" if line.startswith(("+++", "---")) else
               "d-add" if line.startswith("+") else
               "d-del" if line.startswith("-") else "")
        out.append(f'<span class="{cls}">{E(line)}</span>' if cls else E(line))
    return "\n".join(out)
