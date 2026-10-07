"""The console's content pages: home, guide, privacy and sign-in.

Every number on the home page is counted from the study's records at
request time; nothing is typed in by hand.
"""

from __future__ import annotations

import json
import os

from console import data as D
from console.views import E, legend, status


# ----------------------------------------------------------- illustration

def bowl(animate: bool = True) -> str:
    """A tea bowl, broken and mended: three gold seams draw themselves in
    once when the page opens (and are simply there with reduced motion)."""
    cls = "bowl drawing" if animate else "bowl"
    return f"""<svg class="{cls}" viewBox="0 0 420 300" role="img"
aria-label="A tea bowl mended with gold along its cracks">
<ellipse class="bowl-shadow" cx="210" cy="268" rx="132" ry="12"/>
<path class="bowl-body" d="M58 92 C58 92 60 214 150 250 C180 262 240 262 270 250
  C360 214 362 92 362 92 Z"/>
<path class="bowl-foot" d="M168 252 L172 266 L248 266 L252 252"/>
<ellipse class="bowl-rim" cx="210" cy="92" rx="152" ry="26"/>
<ellipse class="bowl-inner" cx="210" cy="94" rx="136" ry="18"/>
<path class="gold g1" pathLength="1" d="M128 106 L141 132 L134 158 L152 183
  L146 214 L162 243"/>
<path class="gold g2" pathLength="1" d="M141 132 L171 143 L187 168"/>
<path class="gold g3" pathLength="1" d="M300 103 L287 129 L296 152 L280 176
  L291 205 L276 231"/>
<path class="gold g4" pathLength="1" d="M226 119 L232 144 L221 170"/>
</svg>"""


# ------------------------------------------------------------------ home

def _supp_first_attempt(supp_dir: str) -> tuple[float, float] | None:
    try:
        with open(os.path.join(supp_dir, "analysis.json")) as fh:
            arms = json.load(fh)["arms"]
        return (arms["C3 (no memory, main study)"]["first_attempt_rate"],
                arms["C3-mem"]["first_attempt_rate"])
    except Exception:
        return None


def home(trials: list[dict], cert_count: int, supp_dir: str) -> str:
    check = D.integrity(trials)
    by_bug = D.leave_alone_by_bug(trials)
    bad_all = sum(t["outcome"] == "false_fix" for t in trials)
    summaries = D.by_approach(trials)
    rollback = summaries.get("C1")
    memory = _supp_first_attempt(supp_dir)

    findings = [
        (f"{bad_all}", f"bad fixes went live, across all {len(trials):,} tests",
         "Neither AI approach ever put a fix live that left the app broken, "
         "with or without the safety check.", "/results#compare-title"),
        (f"{by_bug['every_run']} of {by_bug['risky']}",
         "risky bugs left alone, in every run",
         "The safety check held back on exactly the bugs it could not test, "
         + ("and never on a bug it could have fixed."
            if not by_bug["fixable_wrongly"] else
            f"but also on {by_bug['fixable_wrongly']} it could have fixed."),
         "/results#grid-title"),
    ]
    if memory:
        findings.append(
            (f"{round(memory[1] * 100)}%", "fixed on the first try with memory",
             f"Up from {round(memory[0] * 100)}% when Kintsugi had no memory of "
             "fixing that bug before.", "/guide#glossary"))
    if rollback and rollback.time is not None:
        findings.append(
            (f"{rollback.time:.1f} s", "for a restart and roll back",
             "The old way was fastest here, because a working older version "
             "always existed. Kintsugi is for when it does not.",
             "/results#compare-title"))
    cards = "".join(
        f'<a class="finding" href="{href}"><span class="finding-num">{E(num)}</span>'
        f'<span class="finding-what">{E(what)}</span>'
        f'<span class="finding-why">{E(why)}</span></a>'
        for num, what, why, href in findings)

    integrity = ("" if check["ok"] else
                 '<p class="integrity bad" role="alert">The test records do not '
                 'add up. See the <a href="/results">results page</a> for details.</p>')

    return f"""
<section class="home-hero" aria-labelledby="home-title">
<div class="home-hero-text">
<h1 id="home-title">Software that repairs itself, and proves it</h1>
<p class="lede">Kintsugi watches a live app. When it crashes, Kintsugi makes the
error happen again in a safe copy, lets an AI write a fix, and only puts that fix
live once the error is shown to be gone. Every repair leaves a signed record
anyone can check.</p>
<div class="cta-row"><a class="btn btn-primary" href="/results">See the results</a>
<a class="btn" href="/guide">How to use this site</a></div>
</div>
<div class="home-hero-art">{bowl()}</div>
</section>
{integrity}
<section class="section" aria-labelledby="how-title">
<div class="section-head"><h2 id="how-title">How it works</h2>
<p>Three steps, in this order, every time. If a step cannot be done safely,
Kintsugi stops and hands the problem to a person.</p></div>
<ol class="how">
<li><h3>Catch the error</h3><p>When a request to the app fails, Kintsugi saves
everything about it: what was asked, what the app called, and where it broke.</p></li>
<li><h3>Make it happen again</h3><p>In a safe copy of the app, Kintsugi replays
the same request. If the error does not come back every time, it will not guess.</p></li>
<li><h3>Fix it, with proof</h3><p>An AI writes a fix. It only goes live if the
error is now gone, nothing else broke, and the fix is not just hiding the error.</p></li>
</ol>
</section>

<section class="section" aria-labelledby="found-title">
<div class="section-head"><h2 id="found-title">What the experiment found</h2>
<p>{check["bugs"]} bugs, four ways of dealing with them, {check["tests"]} tests
in the main experiment. These are the headlines; the
<a href="/results">results page</a> has every number behind them.</p></div>
<div class="findings">{cards}</div>
</section>

<section class="section" aria-labelledby="explore-title">
<div class="section-head"><h2 id="explore-title">Explore</h2></div>
<div class="explore">
<a class="tile" href="/results"><h3>Results</h3><p>Every bug and every test,
with filters, so you can check any number yourself.</p></a>
<a class="tile" href="/certificates"><h3>Certificates</h3><p>{cert_count:,}
signed repair records, each one replayable from the record alone.</p></a>
<a class="tile" href="/practice"><h3>Practice</h3><p>How Kintsugi rehearses
bugs while the app is quiet, before they happen for real.</p></a>
<a class="tile" href="/guide"><h3>Guide</h3><p>What the colours mean, how to
search the tests, and how to check a certificate.</p></a>
</div>
</section>

<section class="section name" aria-labelledby="name-title">
<h2 id="name-title">Why “Kintsugi”</h2>
<p>Kintsugi is the Japanese craft of mending broken pottery with gold. The
repair is not hidden: the gold seam shows exactly where the bowl broke and how
it was put back together. This project treats software the same way. Every
repair is kept, visible and checkable, and each one leaves the app a little
stronger than before it broke.</p>
</section>"""


# ----------------------------------------------------------------- guide

GLOSSARY = [
    ("Bug", "A mistake in the app's code that makes some requests fail."),
    ("Approach", "One way of responding when the app fails. Four were compared."),
    ("Run", "One repeat of a test. Each bug was tested three times per approach."),
    ("Fixed", D.OUTCOME_MEANING["recovered"]),
    ("Left alone", D.OUTCOME_MEANING["refused"]),
    ("Gave up", D.OUTCOME_MEANING["escalated"]),
    ("Still broken", D.OUTCOME_MEANING["unrecovered"]),
    ("Bad fix", D.OUTCOME_MEANING["false_fix"]),
    ("Safety check", "Kintsugi's rule that a fix can only go live after the "
     "error has been made to happen again and is then shown to be gone."),
    ("Roll back", "Switching the app back to the version before the bug arrived."),
    ("Certificate", "A signed record of one repair, linked to the record before "
     "it so that any change to it can be detected."),
    ("Practice", "Breaking a private copy of the app on purpose to rehearse a "
     "repair before the bug happens for real."),
    ("Memory", "What Kintsugi kept from earlier repairs, offered to the AI the "
     "next time a similar bug appears."),
    ("Usual time to fix", "The middle value of the seconds from the first error "
     "to the app working again, over fixed tests only."),
]

GUIDE_TOC = [("about", "What this site is"), ("grid", "Reading the grid"),
             ("table", "The comparison table"), ("tests", "Finding one test"),
             ("certificates", "Reading a certificate"),
             ("verify", "Check a certificate yourself"),
             ("operators", "Errors and signing in"),
             ("keys", "Keyboard and display"), ("glossary", "Glossary")]


def guide() -> str:
    toc = "".join(f'<li><a href="#{k}">{E(t)}</a></li>' for k, t in GUIDE_TOC)
    gloss = "".join(f"<dt>{E(t)}</dt><dd>{E(d)}</dd>" for t, d in GLOSSARY)
    return f"""
<div class="page-head"><h1>How to use this site</h1>
<p class="lede">A short guide to reading the results, finding a single test,
and checking Kintsugi's repairs for yourself.</p></div>
<div class="doc">
<nav class="toc" aria-label="On this page"><p class="toc-h">On this page</p><ol>{toc}</ol></nav>
<article class="prose">

<section id="about"><h2>What this site is</h2>
<p>Kintsugi is a platform that repairs a running app when it fails, using AI,
but only when it can prove the repair works. This site shows the experiment
that tested it: 60 bugs, each handled by four different approaches, three
times over.</p>
<p>Everything here is read straight from the experiment's records. Nothing is
typed in by hand, and the results page checks that the records add up before
showing them.</p></section>

<section id="grid"><h2>Reading the grid</h2>
<p>On the <a href="/results">results page</a>, the grid shows every test at
once. Each row is an approach. Each small cell is one bug, and the three thin
bars inside it are its three runs. The colour of a bar is how that run ended.</p>
{legend(["recovered", "refused", "escalated", "unrecovered"])}
<p>Bugs are split into two groups. On the left are bugs that can be fixed. On
the right are bugs that only happen under conditions that cannot be recreated
on demand, such as two requests arriving at the same moment. For those, the
safe choice is to leave them for a person, which is why the bottom row turns
blue there.</p>
<p>Point at a cell, or move to it with the keyboard, to see the bug's name and
what happened in each run. Select it to open that bug's full history.</p></section>

<section id="table"><h2>The comparison table</h2>
<p>Under the grid, the same tests are counted per approach. The small
percentages are out of 180, the number of tests each approach received. A value
shown as <span class="na">n/a</span> means the question does not apply: the two
approaches that never put a code fix live cannot have a bad fix, so the table
says so rather than showing a misleading zero.</p></section>

<section id="tests"><h2>Finding one test</h2>
<p>At the bottom of the results page, <a href="/results#tests">Every test</a>
lists all 720 records. You can:</p>
<ul><li>type part of a bug ID, such as <code>F03</code>, to find it;</li>
<li>narrow the list by approach, result or run;</li>
<li>sort by bug, approach, result or time, by selecting a column heading;</li>
<li>open any test to see its timeline and, where one exists, its certificate.</li></ul>
<p>The address in your browser changes as you filter, so you can copy it to
share exactly what you are looking at.</p></section>

<section id="certificates"><h2>Reading a certificate</h2>
<p>Each time Kintsugi puts a fix live it writes a certificate. Open one from the
<a href="/certificates">certificates page</a> and it reads top to bottom as five
steps: the error that was caught, the proof that it could be repeated, every fix
the AI tried and why any were turned down, the fix that went live, and how the
record was signed and linked to the one before it.</p>
<p>The coloured square at the top is the certificate's seal. It is drawn from
the certificate's ID, so the same certificate always has the same seal, and a
changed certificate would not.</p></section>

<section id="verify"><h2>Check a certificate yourself</h2>
<p>A certificate is only worth something if someone other than Kintsugi can
check it. Two checks are possible:</p>
<ol><li><b>Nothing has been changed.</b> Each certificate carries a fingerprint
of its own contents and of the one before it. Recomputing them shows whether any
record was edited, removed or added.</li>
<li><b>The repair really works.</b> Using only the certificate and a fresh copy
of the broken app, replay the saved request: it should fail before the fix and
succeed after it.</li></ol>
<p>Both checks were run on every certificate from the main experiment. The full
report is public: <a href="/data/verification.json">certificate check report</a>.
With the project's code, the same checks can be run again with
<code>python study/verify_certificates.py</code>.</p></section>

<section id="operators"><h2>Errors and signing in</h2>
<p>The results, certificates, practice log and this guide are public. The
<b>Errors</b> page, which shows failures reported by the live demo app, is for
the project's operator and needs signing in. If you are reviewing the project
and need access, email the author at
<a href="mailto:oluwaseyiabe0@gmail.com">oluwaseyiabe0@gmail.com</a>.</p></section>

<section id="keys"><h2>Keyboard and display</h2>
<table class="keys"><thead><tr><th scope="col">Key</th><th scope="col">What it does</th></tr></thead><tbody>
<tr><td><kbd>Tab</kbd></td><td>Move between links, buttons and the grid</td></tr>
<tr><td><kbd>←</kbd> <kbd>→</kbd> <kbd>↑</kbd> <kbd>↓</kbd></td><td>Move around the grid, one bug or approach at a time</td></tr>
<tr><td><kbd>Home</kbd> <kbd>End</kbd></td><td>Jump to the first or last bug in a row</td></tr>
<tr><td><kbd>Enter</kbd></td><td>Open the selected bug or test</td></tr>
<tr><td><kbd>Esc</kbd></td><td>Close an open panel</td></tr></tbody></table>
<p>The light or dark button in the menu switches the colour theme and remembers
your choice on this device. If your device is set to reduce motion, the site's
small animations are switched off.</p></section>

<section id="glossary"><h2>Glossary</h2><dl class="gloss">{gloss}</dl></section>
</article></div>"""


# --------------------------------------------------------------- privacy

def privacy() -> str:
    return """
<div class="page-head"><h1>Privacy policy</h1>
<p class="lede">The short version: this site does not track you. It sets no
cookies unless you sign in, uses no analytics or advertising, and loads nothing
from other companies.</p>
<p class="faint small">Last updated 3 October 2026.</p></div>
<article class="prose narrow">

<section><h2>Who runs this site</h2>
<p>This site is part of an MSc Computer Science project at the University of
Lagos by Abe Oluwaseyi Elizabeth. It presents the results of a research
experiment and is not a commercial service.</p></section>

<section><h2>What is collected when you visit</h2>
<ul>
<li><b>No cookies for visitors.</b> Reading the site sets no cookies.</li>
<li><b>Your theme choice.</b> If you switch between light and dark, that choice is
saved in your own browser's storage on your device. It is never sent to the
server, and you can clear it at any time by clearing this site's data in your
browser.</li>
<li><b>Server logs.</b> Like every website, the hosting provider (Railway) records
basic technical details of each request, such as the address requested, the
time, and the network address it came from, to keep the service running and
secure. These logs are kept by the provider for a limited period and are not
used for anything else.</li>
<li><b>No third parties.</b> Fonts and all other files are served from this site
itself. There are no analytics, advertising, social media widgets or tracking
pixels.</li>
</ul></section>

<section><h2>If you sign in</h2>
<p>Signing in is only for the project's operator. When the operator signs in,
the site sets one cookie, <code>kintsugi_session</code>, which proves the
sign-in for up to eight hours. It is signed so that it cannot be forged, cannot
be read by scripts on the page, and is only sent over a secure connection.
Signing out deletes it. To protect against password guessing, the site briefly
remembers the network address of repeated failed attempts; this is held in
memory only and forgotten within ten minutes.</p></section>

<section><h2>The data shown on this site</h2>
<p>The results come from demo apps built for the experiment, with made-up
orders and prices. They contain no information about real people. When the
platform records a failing request, it removes sensitive fields such as
passwords and access keys before anything is stored.</p></section>

<section><h2>Your rights</h2>
<p>Because the site collects no personal information about visitors beyond the
hosting provider's standard logs, there is normally nothing held about you. If
you have a question about privacy, or believe something here is wrong, email
the author at <a href="mailto:oluwaseyiabe0@gmail.com">oluwaseyiabe0@gmail.com</a>.
This policy is written with the Nigeria Data Protection Act 2023 in mind.</p></section>

<section><h2>Changes</h2>
<p>If this policy changes, the date at the top of the page will change with it.</p></section>
</article>"""


# ----------------------------------------------------------------- login

def login(token: str, next_url: str, *, error: str = "", user: str = "",
          configured: bool = True, locked: int = 0) -> str:
    if not configured:
        notice = ('<p class="form-note" role="status">Signing in is switched off '
                  "on this copy of the site because no operator password has been "
                  "set. Everything public is available without signing in.</p>")
        disabled = " disabled"
    elif locked:
        mins = max(1, round(locked / 60))
        notice = (f'<p class="form-error" role="alert">Too many attempts. For '
                  f"your security, try again in about {mins} minute"
                  f'{"s" if mins != 1 else ""}.</p>')
        disabled = " disabled"
    else:
        notice = (f'<p class="form-error" role="alert">{E(error)}</p>' if error else "")
        disabled = ""
    return f"""
<div class="auth">
<div class="auth-card">
<h1>Sign in</h1>
<p class="muted">For the project's operator. Results, certificates and the guide
are public and need no account.</p>
{notice}
<form method="post" action="/login" class="auth-form" novalidate>
<input type="hidden" name="csrf" value="{E(token)}">
<input type="hidden" name="next" value="{E(next_url)}">
<div class="field"><label for="user">User name</label>
<input id="user" name="user" type="text" autocomplete="username" required
value="{E(user)}" autocapitalize="none" spellcheck="false"{disabled}></div>
<div class="field"><label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password"
required{disabled}></div>
<button class="btn btn-primary" type="submit"{disabled}>Sign in</button>
</form>
<p class="faint small">Signing in sets one cookie that lasts up to eight hours.
<a href="/privacy">Privacy policy</a></p>
</div>
<div class="auth-art" aria-hidden="true">{bowl(animate=False)}</div>
</div>"""


# ------------------------------------------------------------------ live

def live(entries: list, recordings: list[dict], *, live_enabled: bool,
         repairs: int, csrf: str, signed_in: bool, needs_sign_in: bool) -> str:
    """The Live view: break a bug, watch the platform mend it."""
    by_entry: dict[str, dict] = {}
    for r in recordings:                      # newest first: keep the latest
        by_entry.setdefault(r["entry"], r)
    fixable = [e for e in entries if e.expected_c3 == "healed"]
    alone = [e for e in entries if e.expected_c3 != "healed"]
    if not live_enabled:                      # replays only: offer recorded bugs
        fixable = [e for e in fixable if e.id in by_entry]
        alone = [e for e in alone if e.id in by_entry]

    def opts(group):
        return "".join(f'<option value="{E(e.id)}"{" selected" if e.id == "F03" else ""}>'
                       f'{E(e.id)}: {E(e.title)}</option>' for e in group)

    rec_json = E(json.dumps({k: v["name"] for k, v in by_entry.items()}))
    rec_meta = E(json.dumps({k: {"recorded": v.get("recorded"), "model": v.get("model")}
                             for k, v in by_entry.items()}))
    can_run = live_enabled and (signed_in or not needs_sign_in)
    if live_enabled and not can_run:
        mode_note = ('Live runs need the operator to <a href="/login?next=/live">sign in</a>. '
                     "Recorded runs can be replayed now.")
    elif live_enabled:
        mode_note = "Live: this runs the real platform and the real AI model, now."
    else:
        mode_note = ("Replay: this copy of the site has no test rig or AI key, so "
                     "each run shown is a recording of a real live run, played back "
                     "at the pace it happened.")
    empty = not (fixable or alone)
    return f"""
<div class="live" data-live="{"1" if can_run else "0"}" data-recordings="{rec_json}"
     data-meta="{rec_meta}" data-repairs="{repairs}" data-csrf="{E(csrf)}">
<div class="live-head">
<div><h1>Break it, and watch it mend</h1>
<p class="muted">Pick a bug, plant it in the running shop, and watch Kintsugi catch
it, prove it, fix it and sign for it.</p></div>
<form class="live-controls" id="live-form" onsubmit="return false">
<label class="sr-only" for="live-bug">Bug to plant</label>
<select id="live-bug"{" disabled" if empty else ""}>
<optgroup label="Bugs that can be fixed">{opts(fixable)}</optgroup>
<optgroup label="Bugs it should leave alone">{opts(alone)}</optgroup>
</select>
<button class="btn btn-primary break" id="live-go" type="button"{" disabled" if empty else ""}>Break it</button>
{'<button class="btn" id="live-replay" type="button">Replay a recorded run</button>' if live_enabled else ""}
</form></div>
<p class="live-mode" id="live-mode">{mode_note}</p>

<div class="live-grid">
<section class="panel traffic" aria-labelledby="t-title">
<div class="panel-head"><h2 id="t-title">Live traffic to the shop</h2>
<span class="state" id="live-state" role="status" aria-live="polite">Waiting</span></div>
<canvas id="live-canvas" aria-label="Each dot is one request to the shop: green succeeded, red failed." role="img"></canvas>
<div class="rate"><span>Failing requests</span><canvas id="live-rate" aria-hidden="true"></canvas>
<b id="live-rate-num">0%</b></div>
</section>

<section class="panel steps-panel" aria-labelledby="s-title">
<div class="panel-head"><h2 id="s-title">What Kintsugi is doing</h2>
<span class="clock" id="live-clock">0.0 s</span></div>
<ol class="lsteps" id="live-steps">
<li data-step="fault"><b>The bug goes live</b><span>A new version with the bug replaces the working one.</span></li>
<li data-step="captured"><b>The error is caught</b><span id="st-captured">Kintsugi records the failing request.</span></li>
<li data-step="replay"><b>It is repeated on purpose</b><span id="st-replay">Five replays in a safe copy must all fail.</span>
<i class="dots" id="st-dots"><i></i><i></i><i></i><i></i><i></i></i></li>
<li data-step="ai"><b>The AI writes a fix</b><span id="st-ai">The model sees the evidence and proposes a patch.</span></li>
<li data-step="gate"><b>The fix is checked</b><span id="st-gate">It must apply, stop the error, break nothing and not hide the error.</span>
<i class="chips" id="st-chips"></i></li>
<li data-step="deploy"><b>The fix goes live</b><span id="st-deploy">Only a signed, checked fix can be deployed.</span></li>
<li data-step="recovered"><b>The app works again</b><span id="st-recovered">Checked on the failing request and on live traffic.</span></li>
<li data-step="certificate"><b>A certificate is signed</b><span id="st-cert">A tamper-evident record of the whole repair.</span></li>
</ol>
</section>

<section class="panel patch" aria-labelledby="p-title">
<div class="panel-head"><h2 id="p-title">The AI's patch</h2><span class="muted small" id="p-attempt"></span></div>
<pre id="live-patch" aria-live="off"><span class="muted">The patch appears here as the model writes it.</span></pre>
</section>

<section class="panel mend" aria-labelledby="m-title">
<div class="panel-head"><h2 id="m-title">Mended in gold</h2>
<span class="muted small"><b id="live-repairs">{repairs:,}</b> repairs</span></div>
<div class="mend-art"><svg id="live-bowl" viewBox="0 0 420 300" role="img"
aria-label="A bowl with one gold seam for every repair Kintsugi has made"></svg>
<div class="stamp" id="live-stamp" hidden></div></div>
<p class="muted small" id="live-outcome">Every repair adds a seam.</p>
</section>
</div>
</div>"""
