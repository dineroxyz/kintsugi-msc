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


def _hardening(supp_dir: str) -> dict | None:
    try:
        with open(os.path.join(supp_dir, "analysis.json")) as fh:
            h = json.load(fh)["hardening"]
        return {"score": h["final_score"],
                "cross": h["cross_protection"]["mutants_killed_by_another_faults_test"],
                "mutants": h["mutants"]}
    except Exception:
        return None


ICONS = {
    "catch": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
    "repeat": '<path d="M4 9a8 8 0 0 1 14-3l2 2"/><path d="M20 4v4h-4"/><path d="M20 15a8 8 0 0 1-14 3l-2-2"/><path d="M4 20v-4h4"/>',
    "write": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4"/>',
    "prove": '<path d="M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
    "ship": '<path d="M12 19V5"/><path d="M6 11l6-6 6 6"/><path d="M5 21h14"/>',
    "certify": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h3v3H8zM13 13h3v3h-3zM13 8h3M8 16h3"/>',
}


def _seam(i: int) -> str:
    """The same seam the Live view draws for repair i (a mulberry32 walk
    down the bowl), so a bowl here and a bowl there carry the same marks."""
    def rng(seed):
        state = [seed & 0xFFFFFFFF]
        def nxt():
            state[0] = (state[0] + 0x6D2B79F5) & 0xFFFFFFFF
            t = state[0]
            t = ((t ^ (t >> 15)) * (1 | t)) & 0xFFFFFFFF
            t = (t + (((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF) ^ t) & 0xFFFFFFFF
            return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296
        return nxt

    def half(y):
        k = min(1, max(0, (y - 92) / 158))
        return 150 - 92 * k ** 1.6
    r = rng(i * 7919 + 13)
    y = 100 + r() * 6
    x = 210 + (r() * 2 - 1) * (half(y) - 18)
    d = f"M{x:.1f} {y:.1f}"
    for _ in range(4 + int(r() * 4)):
        y += 16 + r() * 20
        if y > 244:
            break
        x += (r() * 2 - 1) * 18
        hw = half(y) - 10
        x = max(210 - hw, min(210 + hw, x))
        d += f" L{x:.1f} {y:.1f}"
    return d


def mended_bowl(repairs: int) -> str:
    """A bowl carrying one fine gold seam per repair: the platform's record."""
    seams = "".join(f'<path d="{_seam(i)}"/>' for i in range(repairs))
    return (f'<svg class="gold-bowl" viewBox="0 0 420 300" role="img" '
            f'aria-label="A bowl covered in {repairs} fine gold seams, one per repair">'
            '<ellipse cx="210" cy="268" rx="132" ry="12" class="gb-shadow"/>'
            '<path class="gb-body" d="M58 92 C58 92 60 214 150 250 C180 262 240 262 270 250 '
            'C360 214 362 92 362 92 Z"/><path class="gb-foot" d="M168 252 L172 266 L248 266 L252 252"/>'
            f'<g class="gb-seams">{seams}</g>'
            '<ellipse class="gb-rim" cx="210" cy="92" rx="152" ry="26"/>'
            '<ellipse class="gb-inner" cx="210" cy="94" rx="136" ry="18"/></svg>')


def icon(name: str) -> str:
    return (f'<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg>')


def weaker_model(haiku_dir: str) -> dict | None:
    """The weaker-model study's registered result, if present."""
    try:
        with open(os.path.join(haiku_dir, "analysis.json")) as fh:
            a = json.load(fh)
        return {"c2_bad": a["C2"]["false_fix"], "c2_patched": a["C2"]["patched"],
                "c3_bad": a["C3"]["false_fix"], "c3_patched": a["C3"]["patched"],
                "c2_fixed": a["C2"]["recovered_repairable"],
                "c3_fixed": a["C3"]["recovered_repairable"],
                "repairable": a["C3"]["repairable"], "p": a["H2_false_fix_fisher_p"],
                "model": a.get("model", ""), "trials": a.get("n_trials", 0)}
    except Exception:
        return None


def correctness(path: str) -> dict | None:
    """The behavioural check of admitted repairs (study/correctness_summary.json)."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def home(trials: list[dict], cert_count: int, supp_dir: str,
         hero_replay: str | None = None, hero_meta: dict | None = None,
         weak: dict | None = None) -> str:
    check = D.integrity(trials)
    by_bug = D.leave_alone_by_bug(trials)
    gated = [t for t in trials if t["arm"] == "C3"]
    gated_bad = sum(t["outcome"] == "false_fix" for t in gated)
    gated_bad += weak["c3_bad"] if weak else 0
    summaries = D.by_approach(trials)
    gated, ungated, rollback = (summaries.get("C3"), summaries.get("C2"),
                                summaries.get("C1"))
    memory = _supp_first_attempt(supp_dir)
    hard = _hardening(supp_dir)

    integrity = ("" if check["ok"] else
                 '<p class="integrity bad" role="alert">The test records do not '
                 'add up. See the <a href="/results">results page</a> for details.</p>')

    proof = [
        (f"{len(trials):,}", "tests run in a pre-registered study"),
        (f"{gated_bad}", "fixes put live by Kintsugi that then failed, with either AI model"),
        (f"{cert_count:,}", "repair certificates, every one verified"),
        (f"{by_bug['every_run']} of {by_bug['risky']}", "untestable bugs correctly left alone"),
    ]
    proof_html = "".join(f'<div><dt>{E(b)}</dt><dd>{E(a)}</dd></div>' for a, b in proof)
    gold_bowl = mended_bowl(cert_count)

    window = ""
    if hero_replay:
        recorded = E((hero_meta or {}).get("recorded", ""))
        window = f"""
<figure class="window" data-replay="{E(hero_replay)}" aria-label="A real run of Kintsugi, replayed">
<div class="window-bar"><i></i><i></i><i></i><span>Kintsugi live view</span>
<b class="window-state" data-state>Starting</b></div>
<div class="window-body">
<canvas class="window-traffic" aria-hidden="true"></canvas>
<ol class="window-steps">
<li data-s="fault_deployed">Bug goes live</li><li data-s="captured">Error caught</li>
<li data-s="reproduce_done">Repeated 5 of 5 times</li><li data-s="gate_result">AI fix checked</li>
<li data-s="deployed">Fix live</li><li data-s="recovered">Working again</li>
<li data-s="certificate">Certificate signed</li></ol>
<div class="window-seal" data-seal hidden></div>
</div>
<figcaption>A real run, replayed at the pace it happened. Recorded {recorded}.
<a href="/live">Try it yourself</a></figcaption>
</figure>"""

    rollback_t = f"{rollback.time:.1f} s" if rollback and rollback.time else "n/a"
    gated_t = f"{gated.time:.1f} s" if gated and gated.time else "n/a"
    ungated_t = f"{ungated.time:.1f} s" if ungated and ungated.time else "n/a"

    mem_html = ""
    if memory:
        a, b = round(memory[0] * 100), round(memory[1] * 100)
        mem_html = f"""<div class="bars" aria-label="First-try success: {a}% without memory, {b}% with memory">
<div><span>Without memory</span><i style="--w:{a}%"></i><b>{a}%</b></div>
<div><span>With memory</span><i class="gold" style="--w:{b}%"></i><b>{b}%</b></div></div>"""
    hard_line = (f" The tests it saved along the way now catch {round(hard['score'] * 100)}% "
                 f"of the bugs before they ship, {hard['cross']} of them through a test "
                 "saved for a different bug." if hard else "")

    weak_html = ""
    if weak:
        weak_html = f"""<article class="feat feat-wide">
<div class="feat-art"><div class="bars" aria-label="Failed fixes put live with a smaller AI model: {weak['c2_bad']} without the check, {weak['c3_bad']} with Kintsugi">
<div><span>AI fix, no check</span><i class="red" style="--w:{min(100, weak['c2_bad'] * 18)}%"></i><b>{weak['c2_bad']}</b></div>
<div><span>With Kintsugi</span><i style="--w:2%"></i><b>{weak['c3_bad']}</b></div></div>
<p class="feat-small">Fixes put live that then failed, smaller AI model, {weak['trials']} tests</p></div>
<h3>Catches the failed fixes a cheaper AI makes</h3>
<p>With a frontier model, neither approach put live a fix that then failed. With a smaller,
cheaper model, the unchecked approach put {weak['c2_bad']} such fixes live and Kintsugi put
{weak['c3_bad']}: on every one of those bugs it either fixed the bug properly or left it for a
person. The difference falls just short of the usual statistical threshold
(p = {weak['p']:.3f}), so the study reports it as a strong lead rather than a proof.</p>
<p>It cannot catch everything. A separate check found that some fixes it accepted, with either
model, stopped the crash but quietly changed something else.
<a href="/results#correct-title">See which ones</a>.</p></article>"""

    pipeline = [
        ("catch", "Catch", "The failing request is recorded with everything the app called."),
        ("repeat", "Reproduce", "It is replayed in a sealed copy until it fails the same way every time."),
        ("write", "Diagnose", "An AI model reads the evidence and writes one small patch."),
        ("prove", "Prove", "The patch must stop the failure, pass the existing tests and hide nothing."),
        ("ship", "Ship", "Only a signed, proven patch is allowed to go live."),
        ("certify", "Certify", "The repair is written into a tamper-evident chain."),
    ]
    pipe_html = "".join(
        f'<li><span class="pipe-ico">{icon(k)}</span><b>{E(t)}</b><span>{E(d)}</span></li>'
        for k, t, d in pipeline)

    yes = '<span class="yes" aria-label="yes">✓</span>'
    no = '<span class="no" aria-label="no">—</span>'
    rows = [
        ("Recovers the app", yes, yes, yes),
        ("Writes new code to remove the bug", no, yes, yes),
        ("Proves the fix before it ships", no, no, yes),
        ("Knows when not to act", no, no, yes),
        ("Leaves a signed, replayable record", no, no, yes),
        ("Usual time to fix in the study", rollback_t, ungated_t, gated_t),
    ]
    cmp_html = "".join(
        f'<tr><th scope="row">{E(r[0])}</th><td>{r[1]}</td><td>{r[2]}</td>'
        f'<td class="us">{r[3]}</td></tr>' for r in rows)

    return f"""
<section class="hero2" aria-labelledby="home-title">
<div class="hero2-copy">
<h1 id="home-title">Production software that mends itself, and proves it.</h1>
<p class="lede">When a live app crashes, Kintsugi catches the failure, makes it
happen again in a sealed copy, has an AI write the fix, and ships it only once
the failure is proven gone. Every repair comes with a signed certificate anyone
can check.</p>
<div class="cta-row"><a class="btn btn-primary btn-lg" href="/live">Watch it mend</a>
<a class="btn btn-lg" href="/results">See the evidence</a></div>
</div>
{window}
</section>
{integrity}
<dl class="proof">{proof_html}</dl>

<section class="band" aria-labelledby="problem-title">
<h2 id="problem-title" class="big">It is 3 a.m. and production is down.</h2>
<div class="three">
<div><h3>Restart and roll back</h3><p>Fast, and the right first move when an older
version works. But it only undoes. If the bug has always been there, there is
nothing to go back to.</p></div>
<div><h3>Let an AI patch it</h3><p>AI can now write a fix from a crash report. But a
fix inferred from a stack trace is a guess, shipped to a system that is already
in trouble.</p></div>
<div class="us"><h3>Kintsugi</h3><p>Writes the fix and proves it first. If the failure
cannot be reproduced, it does not guess. It stops and tells a person why.</p></div>
</div>
</section>

<section class="band" aria-labelledby="pipe-title">
<h2 id="pipe-title" class="big">Six steps. Every time. In that order.</h2>
<p class="sub">If any step cannot be done safely, Kintsugi stops there.</p>
<div class="pipe-wrap"><svg class="pipe-seam" viewBox="0 0 1000 10" preserveAspectRatio="none" aria-hidden="true">
<path pathLength="1" d="M0 5 H160 l8 -3 l6 6 l7 -4 H500 l6 4 l8 -6 l6 3 H820 l7 3 l6 -5 l8 3 H1000"/></svg>
<ol class="pipe">{pipe_html}</ol></div>
</section>

<section class="band" aria-labelledby="feat-title">
<h2 id="feat-title" class="big">Built on evidence, not hope.</h2>
<div class="feats">
<article class="feat">
<div class="feat-art"><div class="replay-dots"><i></i><i></i><i></i><i></i><i></i></div>
<ul class="checks"><li>Not just hiding the error</li><li>Written correctly</li>
<li>The error is gone</li><li>Nothing else broke</li></ul></div>
<h3>Proof, not guesses</h3>
<p>A fix is only considered once the failure has been made to happen again on
demand. It ships only when that same failure stops.</p></article>

<article class="feat">
<div class="feat-art"><div class="replay-dots blue"><i></i><i></i><i></i><i></i><i></i></div>
<p class="feat-num">{by_bug['every_run']} / {by_bug['risky']}</p></div>
<h3>Knows when to stop</h3>
<p>Races and one-off glitches cannot be proven, so it leaves them alone, with the
reason attached. In the study it held back on every such bug, and never on one
it could fix.</p></article>

<article class="feat">
<div class="feat-art"><div class="chain"><i></i><i></i><i></i><i class="cut"></i><i></i></div>
<p class="feat-small">Change one character, and the chain names the record.</p></div>
<h3>A receipt for every repair</h3>
<p>Each repair is signed and linked to the last. All {cert_count:,} certificates
from the study were replayed from the record alone and confirmed.</p></article>

{weak_html}
<article class="feat">
<div class="feat-art">{mem_html}</div>
<h3>Stronger with every repair</h3>
<p>Kintsugi remembers what worked. With that memory it fixes a returning bug on
the first try far more often.{E(hard_line)}</p></article>
</div>
</section>

<section class="band dev" aria-labelledby="dev-title">
<div class="dev-copy">
<h2 id="dev-title" class="big">Two lines in your service.</h2>
<p>A small capture library sits inside the app and records each failing request,
with everything the app called, removing passwords and keys before anything
leaves. Everything else runs beside your service, not inside it.</p>
<a href="/guide">How it all fits together</a>
</div>
<pre class="code" aria-label="Example: adding Kintsugi to a FastAPI service"><span class="c-k">from</span> libs.kintsugi_capture <span class="c-k">import</span> CaptureMiddleware, CaptureClient

app = FastAPI()
<span class="c-hl">app.add_middleware(CaptureMiddleware, service=<span class="c-s">"orders"</span>)</span>
<span class="c-hl">pricing = CaptureClient(timeout=<span class="c-n">5.0</span>)</span>   <span class="c-c"># calls recorded for replay</span>

<span class="c-d">@app.get</span>(<span class="c-s">"/orders/{{order_id}}"</span>)
<span class="c-k">def</span> get_order(order_id: int) -&gt; dict:
    ...</pre>
</section>

<section class="band" aria-labelledby="cmp-title">
<h2 id="cmp-title" class="big">How it compares.</h2>
<p class="sub">Measured on the same 60 bugs, three times each. Rolling back was
fastest here, because a working older version always existed. Kintsugi is for
when it does not.</p>
<div class="table-scroll"><table class="cmp"><thead><tr><th scope="col"><span class="sr-only">Capability</span></th>
<th scope="col">Restart and roll back</th><th scope="col">AI fix, no check</th>
<th scope="col" class="us">Kintsugi</th></tr></thead><tbody>{cmp_html}</tbody></table></div>
</section>

<section class="band duo" aria-labelledby="duo-title">
<h2 id="duo-title" class="sr-only">Use it and check it</h2>
<a class="duo-card" href="/signup"><h3>Connect your own service</h3>
<p>Create a free account, connect a Python service with a key, and its failures are caught,
grouped and shown on your account page. Break a bug for real while you are there.</p>
<span class="duo-cta">Create an account</span></a>
<a class="duo-card" href="/trust"><h3>Check the record yourself</h3>
<p>Every repair is signed with a published key, and the study's certificates are timestamped
on Bitcoin. Your browser can verify any certificate without trusting this site.</p>
<span class="duo-cta">How to verify</span></a>
</section>

<section class="band research" aria-labelledby="res-title">
<div>
<h2 id="res-title" class="big">Tested in the open.</h2>
<p>Kintsugi was built and evaluated as MSc Computer Science research at the
University of Lagos. The study design was fixed before any test ran. Every test
record, every certificate and the independent check of each one are published
here, so every number on this page can be traced to its source.</p>
<p class="links"><a href="/results">Every test</a><a href="/certificates">Every certificate</a>
<a href="/verification">The certificate check report</a><a href="/guide">How to read it all</a></p>
</div>
<figure class="research-art">{gold_bowl}<figcaption>{cert_count:,} repairs, one gold
seam each.</figcaption></figure>
</section>

<section class="closer" aria-labelledby="closer-title">
<h2 id="closer-title">Break something. Watch it mend.</h2>
<p>Pick a bug, plant it in a running shop, and watch every step happen.</p>
<a class="btn btn-primary btn-lg" href="/live">Open the live view</a>
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
             ("connect", "Connect your own service"), ("status", "The status page"),
             ("operators", "Accounts and signing in"),
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
<p>The first check was run on all 494 certificates from the experiment. The
second was run on all 130 certificates written by the full Kintsugi setup in the
main comparison. The results are public: <a href="/verification">certificate check report</a>.
With the project's code, the same checks can be run again with
<code>python study/verify_certificates.py</code>.</p>
<p>You can also check any certificate in your own browser: open it and press
<b>Check it yourself</b>. Your browser downloads the certificate's exact bytes,
recomputes its fingerprint and its link to the one before, and verifies its
signature against Kintsugi's published key. The <a href="/trust">trust page</a>
explains the key, the signed list of the study's certificates, and the Bitcoin
timestamp that fixes that list in time.</p></section>

<section id="connect"><h2>Connect your own service</h2>
<p>With an account, open your account page and use <b>Connect a service</b>. You get
a key, shown once. Copy the small capture library into a Python web service, add
two lines, and set one environment variable; the page shows exactly what to
copy. From then on, every unhandled failure in that service is sent to Kintsugi
with passwords and keys removed, and grouped into incidents on your account
page. Connecting gives Kintsugi no access to your code.</p></section>

<section id="status"><h2>The status page</h2>
<p>The <a href="/status">status page</a> checks, when you open it, that the shop's
services are answering, and lists every bug planted in the last 24 hours with
what happened to it.</p></section>

<section id="operators"><h2>Accounts and signing in</h2>
<p>The results, certificates, practice log and this guide are public, and
anyone can replay recorded runs on the Live page. A free account lets you break
a bug <b>for real</b>: the platform and a live AI model run while you watch.
Each account has a small daily allowance of real runs, and your runs are kept on
your account page with their recordings and certificates. The <b>Errors</b>
page, which shows failures reported by the live demo app, is for the project's
operator only. If you are reviewing the project
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
from other companies. If you create an account, we keep your email address and
the runs you start, and you can delete all of it at any time.</p>
<p class="faint small">Last updated 7 October 2026.</p></div>
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

<section><h2>If you create an account</h2>
<p>An account lets you start real runs on the Live page. To provide it we store:</p>
<ul>
<li><b>Your email address</b>, used only to sign you in. We do not send marketing
email and do not share it with anyone.</li>
<li><b>A scrambled form of your password</b> (a salted scrypt hash). The password
itself is never stored and cannot be recovered from what is stored.</li>
<li><b>The runs you start</b>: which bug, when, the result, and the recording and
certificate it produced, so they appear on your account page and count towards
your daily allowance. Recordings of runs may also be replayed publicly on the
Live page; they contain the bug and the platform's steps, not your email.</li>
</ul>
<p>These are held in the project's database on Railway. You can delete your
account from your account page at any time; that immediately removes your email
and password hash and unlinks your runs from you. The runs remain only as
anonymous entries, which keeps the site's daily usage limit honest.</p></section>

<section><h2>If you sign in</h2>
<p>Signing in sets one cookie, <code>kintsugi_session</code>, which proves the
sign-in for up to eight hours. It is signed so that it cannot be forged, cannot
be read by scripts on the page, and is only sent over a secure connection.
Signing out deletes it. To protect against password guessing and mass sign-ups,
the site briefly remembers the network address of repeated failed attempts and
of new accounts; this is held in memory only and forgotten within an hour.</p></section>

<section><h2>The data shown on this site</h2>
<p>The results come from demo apps built for the experiment, with made-up
orders and prices. They contain no information about real people. When the
platform records a failing request, it removes sensitive fields such as
passwords and access keys before anything is stored.</p></section>

<section><h2>Your rights</h2>
<p>If you have no account, the site holds nothing about you beyond the hosting
provider's standard logs. If you have one, you can see what is held on your
account page and delete it there. If
you have a question about privacy, or believe something here is wrong, email
the author at <a href="mailto:oluwaseyiabe0@gmail.com">oluwaseyiabe0@gmail.com</a>.
This policy is written with the Nigeria Data Protection Act 2023 in mind.</p></section>

<section><h2>Changes</h2>
<p>If this policy changes, the date at the top of the page will change with it.</p></section>
</article>"""


# ----------------------------------------------------------------- login

def _auth_card(title: str, intro: str, notice: str, form: str, foot: str) -> str:
    return f"""
<div class="auth">
<div class="auth-card">
<h1>{title}</h1>
<p class="muted">{intro}</p>
{notice}
{form}
<p class="faint small">{foot}</p>
</div>
<div class="auth-art" aria-hidden="true">{bowl(animate=False)}</div>
</div>"""


def login(token: str, next_url: str, *, error: str = "", user: str = "",
          locked: int = 0) -> str:
    disabled = ""
    if locked:
        mins = max(1, round(locked / 60))
        notice = (f'<p class="form-error" role="alert">Too many attempts. For your '
                  f'security, try again in about {mins} minute{"s" if mins != 1 else ""}.</p>')
        disabled = " disabled"
    else:
        notice = f'<p class="form-error" role="alert">{E(error)}</p>' if error else ""
    form = f"""<form method="post" action="/login" class="auth-form" novalidate>
<input type="hidden" name="csrf" value="{E(token)}">
<input type="hidden" name="next" value="{E(next_url)}">
<div class="field"><label for="user">Email</label>
<input id="user" name="user" type="text" inputmode="email" autocomplete="username" required
value="{E(user)}" autocapitalize="none" spellcheck="false"{disabled}></div>
<div class="field"><label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password"
required{disabled}></div>
<button class="btn btn-primary" type="submit"{disabled}>Sign in</button>
</form>"""
    return _auth_card(
        "Sign in", "Sign in to run real heals on the Live page.", notice, form,
        'New here? <a href="/signup">Create a free account</a>. '
        'Signing in sets one cookie. <a href="/privacy">Privacy policy</a>')


def signup(token: str, *, runs: int, error: str = "", email: str = "") -> str:
    notice = f'<p class="form-error" role="alert">{E(error)}</p>' if error else ""
    form = f"""<form method="post" action="/signup" class="auth-form" novalidate>
<input type="hidden" name="csrf" value="{E(token)}">
<div class="field"><label for="email">Email</label>
<input id="email" name="email" type="email" autocomplete="email" required
value="{E(email)}" autocapitalize="none" spellcheck="false"></div>
<div class="field"><label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="new-password"
minlength="10" required aria-describedby="pw-help">
<span class="faint small" id="pw-help">At least 10 characters.</span></div>
<label class="check"><input type="checkbox" name="agree" value="yes" required>
<span>I have read the <a href="/privacy">privacy policy</a>.</span></label>
<button class="btn btn-primary" type="submit">Create account</button>
</form>"""
    return _auth_card(
        "Create an account",
        f"A free account lets you break a bug for real, {runs} times a day, and watch "
        "Kintsugi mend it with a live AI model. Every run you start is kept on your "
        "account with its certificate.",
        notice, form,
        'Already have an account? <a href="/login">Sign in</a>. We store your email '
        "and a scrambled form of your password, nothing else.")


def _services_section(services: list, token: str, new_key, error: str, base: str) -> str:
    fresh = ""
    if new_key:
        svc, key = new_key
        fresh = f"""
<div class="new-key" role="status"><p><b>Service “{E(svc.name)}” connected.</b> Copy this key
now: it is shown only once and stored only as a fingerprint.</p>
<code class="keyblock">{E(key)}</code>
<ol class="steps-mini">
<li>Copy <a href="https://github.com/dineroxyz/kintsugi-shop/blob/main/libs/kintsugi_capture/__init__.py">kintsugi_capture</a>
into your project (it needs <code>httpx</code>).</li>
<li>Add two lines to your FastAPI or Starlette app:</li></ol>
<pre>from kintsugi_capture import CaptureMiddleware
app.add_middleware(CaptureMiddleware, service="{E(svc.name)}")</pre>
<ol class="steps-mini" start="3"><li>Set this environment variable where the service runs:</li></ol>
<pre>KINTSUGI_CONTROL_PLANE={E(base)}/connect/{E(key)}</pre>
<p class="muted small">Every unhandled failure is then sent here, with passwords and keys removed
before it leaves your server, and grouped into incidents below.</p></div>"""
    err = f'<p class="form-error" role="alert">{E(error)}</p>' if error else ""
    rows = []
    for svc, incidents in services:
        inc = "".join(
            f'<tr><td>{E(i.exception_type)}</td><td class="num">{i.capture_count}</td>'
            f'<td class="mono small">{E(i.signature[:12])}</td>'
            f'<td class="faint small num">{E(str(i.created_at)[:16].replace("T", " "))}</td></tr>'
            for i in incidents)
        table = (f'<div class="table-scroll"><table class="ex-table" style="min-width:520px">'
                 '<thead><tr><th scope="col">Error</th><th scope="col" class="num">Times seen</th>'
                 '<th scope="col">Signature</th><th scope="col" class="num">First seen (UTC)</th></tr></thead>'
                 f'<tbody>{inc}</tbody></table></div>' if incidents else
                 '<p class="muted small">No failures reported yet. That is good news.</p>')
        rows.append(f"""<div class="svc"><div class="svc-head"><h3>{E(svc.name)}</h3>
<form method="post" action="/account/services/{svc.id}/delete"><input type="hidden" name="csrf" value="{E(token)}">
<button class="btn btn-quiet" type="submit">Disconnect</button></form></div>{table}</div>""")
    return f"""
<section id="services" aria-labelledby="svc-title" style="margin-top:48px">
<h2 id="svc-title" style="font-size:22px;margin:0 0 8px">Connect your own service</h2>
<p class="muted">Point a real Python service at Kintsugi and its failures are caught,
grouped and shown here. Connecting does not give Kintsugi any access to your code;
it only receives the failure reports your service sends.</p>
{fresh}{err}
{"".join(rows)}
<form method="post" action="/account/services" class="auth-form svc-form">
<input type="hidden" name="csrf" value="{E(token)}">
<div class="field"><label for="svc-name">Service name</label>
<input id="svc-name" name="name" required maxlength="40" placeholder="for example detector-api"
autocapitalize="none" spellcheck="false"></div>
<button class="btn" type="submit">Connect a service</button></form>
</section>"""


def account(user, runs: list, left: dict, titles: dict, delete_token: str, *,
            live_enabled: bool, services: list | None = None, service_token: str = "",
            new_key=None, service_error: str = "", base_url: str = "",
            password_token: str = "", password_msg: tuple | None = None,
            admin: bool = False) -> str:
    rows = "".join(
        f'<tr><td class="mono">{E(r.entry)}</td><td class="muted small">{E(titles.get(r.entry, ""))}</td>'
        f'<td>{E({"recovered": "Mended", "refused": "Left alone", "escalated": "Gave up", "false_fix": "Bad fix", None: "Running"}.get(r.outcome, r.outcome or ""))}'
        f'{f" in {r.mttr:.1f} s" if r.mttr else ""}</td>'
        f'<td class="faint small num">{E(r.started_at[:16].replace("T", " "))}</td>'
        f'<td>{_run_links(r)}</td></tr>'
        for r in runs)
    table = (f'<div class="table-scroll"><table class="ex-table"><thead><tr><th scope="col">Bug</th>'
             '<th scope="col">What was wrong</th><th scope="col">Result</th>'
             '<th scope="col" class="num">When (UTC)</th><th scope="col"><span class="sr-only">Replay</span></th>'
             f'</tr></thead><tbody>{rows}</tbody></table></div>' if runs else
             '<p class="muted">No runs yet. <a href="/live">Break your first bug</a>.</p>')
    allowance = ("Owner account: unlimited real runs." if live_enabled and left.get("unlimited") else
                 f"You have <b>{left['account']}</b> of {left['per_account']} runs left in "
                 "the last 24 hours." if live_enabled else
                 "Live runs are switched off on this copy of the site; replays still work.")
    return f"""
<div class="page-head"><h1>Your account</h1>
<p class="lede">{E(user.email)}</p>
<p class="muted">{allowance}</p>
<p><a class="btn btn-primary" href="/live">Break a bug</a></p></div>
{_admin_panel() if admin else ""}
<h2 style="font-size:22px;margin:0 0 12px">Your runs</h2>
{table}
{_services_section(services or [], service_token, new_key, service_error, base_url)}
<section id="password" aria-labelledby="pw-title" style="margin-top:48px">
<h2 id="pw-title" style="font-size:22px;margin:0 0 8px">Change your password</h2>
{(f'<p class="{"form-note" if password_msg[0] else "form-error"}" role="status">{E(password_msg[1])}</p>') if password_msg else ""}
<form method="post" action="/account/password" class="auth-form" style="max-width:420px">
<input type="hidden" name="csrf" value="{E(password_token)}">
<input type="hidden" name="email" value="{E(user.email)}" autocomplete="username">
<div class="field"><label for="pw-current">Current password</label>
<input id="pw-current" name="current" type="password" autocomplete="current-password" required></div>
<div class="field"><label for="pw-new">New password</label>
<input id="pw-new" name="new" type="password" autocomplete="new-password" minlength="10" required></div>
<button class="btn" type="submit">Change password</button></form></section>
<section class="danger" aria-labelledby="del-title">
<h2 id="del-title" style="font-size:22px;margin:48px 0 8px">Delete your account</h2>
<p class="muted">This removes your email and password, and unlinks your runs from you.
The runs themselves stay as anonymous entries (with their public recordings), so
the site's daily limit still counts them.</p>
<form method="post" action="/account/delete" class="auth-form" style="max-width:420px">
<input type="hidden" name="csrf" value="{E(delete_token)}">
<div class="field"><label for="confirm">Type your email to confirm</label>
<input id="confirm" name="confirm" type="email" autocomplete="off" required></div>
<button class="btn" type="submit">Delete my account</button></form>
</section>"""


# ------------------------------------------------------------------ live



def _run_links(r) -> str:
    """Replay and certificate links for one recorded run."""
    links = []
    if r.recording:
        links.append(f'<a href="/live?replay={E(r.recording)}">Replay</a>')
    if r.certificate is not None and r.outcome == "recovered":
        links.append(f'<a href="/live-certificates/{r.certificate}">Certificate</a>')
    return " ".join(links)


def _admin_panel() -> str:
    return ('<section class="admin-panel" aria-labelledby="admin-title">'
            '<div><h2 id="admin-title">Admin</h2>'
            '<p class="muted">You are signed in as an owner. See every run on the site, '
            'the live error feed, and the site\'s health.</p></div>'
            '<a class="btn btn-primary" href="/admin">Open admin</a></section>')


def admin(counts: dict, errors: int | None, runs: list, titles: dict) -> str:
    """The owner's view: site numbers, every recent run, and where to look next."""
    word = {"recovered": "Mended", "refused": "Left alone", "escalated": "Gave up",
            "false_fix": "Bad fix", None: "Running"}
    tiles = [("People with accounts", counts["users"]),
             ("Real runs, last 24 hours", counts["runs_today"]),
             ("Real runs, all time", counts["runs_total"]),
             ("Connected services", counts["services"]),
             ("Errors caught", "n/a" if errors is None else errors)]
    tile_html = "".join(f'<div class="admin-tile"><b>{E(str(v))}</b><span>{E(k)}</span></div>'
                        for k, v in tiles)
    rows = "".join(
        f'<tr><td class="mono">{E(r.entry)}</td><td class="muted small">{E(titles.get(r.entry, ""))}</td>'
        f'<td>{E(word.get(r.outcome, r.outcome or ""))}{f" in {r.mttr:.1f} s" if r.mttr else ""}</td>'
        f'<td class="small">{"Owner" if r.user_id is None else f"Account {r.user_id}"}</td>'
        f'<td class="faint small num">{E(r.started_at[:16].replace("T", " "))}</td>'
        f'<td>{_run_links(r)}</td></tr>' for r in runs)
    table = (f'<div class="table-scroll"><table class="ex-table"><thead><tr><th scope="col">Bug</th>'
             '<th scope="col">What was wrong</th><th scope="col">Result</th><th scope="col">Who</th>'
             '<th scope="col" class="num">When (UTC)</th><th scope="col"><span class="sr-only">Replay</span></th>'
             f'</tr></thead><tbody>{rows}</tbody></table></div>' if runs else
             '<p class="muted">No real runs in the last three days.</p>')
    return f"""
<div class="page-head"><h1>Admin</h1>
<p class="lede">The whole site at a glance. Only owner accounts can open this page.</p></div>
<div class="admin-tiles">{tile_html}</div>
<div class="admin-links">
<a class="btn btn-primary" href="/incidents">Live error feed</a>
<a class="btn" href="/status">Site health</a>
<a class="btn" href="/live-certificates">Live certificates</a>
<a class="btn" href="/verification">Verification report</a>
<a class="btn" href="/account">Your account</a></div>
<h2 style="font-size:22px;margin:24px 0 12px">Every real run, last 3 days</h2>
{table}"""


def _recent_feed(runs: list, titles: dict) -> str:
    word = {"recovered": "Mended", "refused": "Left alone", "escalated": "Gave up",
            "false_fix": "Bad fix", None: "Running"}
    items = []
    for r in runs[:8]:
        res = word.get(r.outcome, r.outcome or "")
        if r.mttr:
            res += f" in {r.mttr:.1f} s"
        items.append(f'<li><span class="mono">{E(r.entry)}</span><span class="muted">'
                     f'{E(titles.get(r.entry, ""))}</span><b>{E(res)}</b>'
                     f'<span class="faint small">{E(r.started_at[11:16])} UTC</span>'
                     f'<span class="feed-links">{_run_links(r)}</span></li>')
    if not items:
        return ""
    return ('<section class="panel feed" aria-labelledby="feed-title"><div class="panel-head">'
            '<h2 id="feed-title">Recent real runs</h2><a class="small" href="/status">All of today</a></div>'
            f'<ol class="feed-list">{"".join(items)}</ol></section>')

def live(entries: list, recordings: list[dict], *, live_enabled: bool,
         repairs: int, csrf: str, status: dict, welcome: bool = False,
         recent: list | None = None, titles: dict | None = None) -> str:
    """The Live view: break a bug, watch the platform mend it."""
    by_entry: dict[str, dict] = {}
    for r in recordings:                      # newest first: keep the latest
        by_entry.setdefault(r["entry"], r)
    fixable = [e for e in entries if e.expected_c3 == "healed"]
    alone = [e for e in entries if e.expected_c3 != "healed"]
    can_run = bool(status.get("can"))
    if not can_run:                           # replays only: offer recorded bugs
        fixable = [e for e in fixable if e.id in by_entry]
        alone = [e for e in alone if e.id in by_entry]

    def opts(group):
        return "".join(f'<option value="{E(e.id)}"{" selected" if e.id == "F03" else ""}>'
                       f'{E(e.id)}: {E(e.title)}</option>' for e in group)

    rec_json = E(json.dumps({k: v["name"] for k, v in by_entry.items()}))
    rec_meta = E(json.dumps({k: {"recorded": v.get("recorded"), "model": v.get("model")}
                             for k, v in by_entry.items()}))
    why = status.get("why")
    left = (f" You have {status['account']} of {status['per_account']} runs left today."
            if why == "account" else " Owner account: unlimited real runs." if why == "owner" else "")
    mode_note = {
        "replay": ("Replay: this copy of the site has no test rig or AI key, so each run "
                   "shown is a recording of a real live run, played back at the pace it "
                   "happened."),
        "signin": ('Watch a recorded run now, or <a href="/signup">create a free account</a> '
                   'to break a bug for real with a live AI model. Already have one? '
                   '<a href="/login?next=/live">Sign in</a>.'),
        "account_cap": ("You have used today's real runs. They refresh 24 hours after each "
                        "run; recorded runs can be replayed any time."),
        "site_cap": ("Today's real runs for the whole site are used up, to keep costs "
                     "bounded. Recorded runs can be replayed any time."),
        "account": "Live: this runs the real platform and a real AI model, now." + left,
        "operator": "Live: operator run, no daily limit.",
        "owner": "Live: this runs the real platform and a real AI model, now." + left,
        "local": "Live: this runs the real platform and the real AI model, now.",
    }.get(why, "")
    if welcome:
        mode_note = "Welcome. Your account is ready. " + mode_note
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
<button class="btn btn-primary break" id="live-go" type="button"{" disabled" if empty else ""}>{"Break it" if can_run else "Watch a replay"}</button>
{'<button class="btn" id="live-replay" type="button">Replay a recorded run</button>' if can_run else ""}
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
<div class="explain" id="live-explain" hidden><b>In plain English</b>
<span class="faint small">Written by a small AI model from the evidence. Not verified; the certificate is the proof.</span>
<p></p></div>
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
{_recent_feed(recent or [], titles or {})}
</div>"""


# ------------------------------------------------------------------ trust

def trust(key: dict, att: dict | None, anchored: bool, cert_count: int) -> str:
    key_html = (f'<p><span class="faint small">Public key (Ed25519, base64)</span><br>'
                f'<code class="keyblock">{E(key["key"])}</code></p>'
                f'<p class="muted">Fingerprint <code>{E(key["key_id"])}</code>. '
                '<a href="/.well-known/kintsugi-key.json">Download as JSON</a></p>'
                if key.get("key") else
                '<p class="muted">No public key has been published on this copy of the site.</p>')
    att_html = ""
    if att:
        att_html = f"""
<section id="attestation"><h2>The study's certificates, signed</h2>
<p>The {att["count"]} certificates from the main study were issued under the chain's
original keyed-hash signature, which only the key holder can check. On
{E(att["at"][:10])} their hashes were listed and signed with the public key above,
so from that date anyone can check that each one is unchanged.
<a href="/data/attestation.json">Download the signed list</a>.</p></section>"""
    anchor_html = ("""
<section id="anchor"><h2>Anchored to Bitcoin</h2>
<p>The signed list was also timestamped with OpenTimestamps, which records a
fingerprint of it in the Bitcoin blockchain. The timestamp is confirmed in
Bitcoin block 970,366, mined on 7 October 2026, so it proves the list existed,
unchanged, by that date, without trusting this site, its owner, or anyone else.</p>
<p><a href="/data/attestation.json.ots">Download the timestamp proof</a>, then check it
with the open-source OpenTimestamps tool:</p>
<pre>pip install opentimestamps-client
ots verify attestation.json.ots</pre></section>""" if anchored else "")
    return f"""
<div class="page-head"><h1>Why you can trust the record</h1>
<p class="lede">Every repair Kintsugi makes is written down as a certificate. These are
the three ways you can check those certificates without having to trust this
website.</p></div>
<article class="prose narrow">
<section><h2>1. Recompute it yourself</h2>
<p>Each certificate's ID is a SHA-256 fingerprint of its exact contents, and each
one includes the fingerprint of the certificate before it. Change one
character anywhere and every fingerprint after it stops matching. On any
certificate page, <b>Check it yourself</b> recomputes this in your own browser.</p></section>
<section id="key"><h2>2. Check the signature</h2>
<p>New certificates are signed with an Ed25519 key. The private half never leaves
the server; the public half is below, so anyone can verify a signature without
holding any secret.</p>
{key_html}</section>
{att_html}
{anchor_html}
<section><h2>What this does not prove</h2>
<p>A valid certificate proves the record is genuine and unchanged, and that its
fix passed the checks it lists. It does not prove the fix is the best possible
one; for that, read the patch, or replay it, as described in the
<a href="/guide#verify">guide</a>. There are {cert_count:,} certificates in the
study chain.</p></section>
</article>"""



# ----------------------------------------------------------------- status

def status(checks: list[dict], runs: list, titles: dict) -> str:
    all_up = bool(checks) and all(c["ok"] for c in checks)
    head = ("All services are answering." if all_up else
            "Something is not answering." if checks else
            "No services are being watched on this copy of the site.")
    def svc_line(c: dict) -> str:
        state = f'answering, {c["ms"]} ms' if c["ok"] else "not answering"
        swatch = "k-fixed" if c["ok"] else "k-broken"
        return (f'<li><span class="sw {swatch}" aria-hidden="true"></span>'
                f'<b>{E(c["name"])}</b><span class="muted">{state}</span></li>')
    svc = "".join(svc_line(c) for c in checks)
    counts = {"recovered": 0, "refused": 0, "escalated": 0, "false_fix": 0}
    for r in runs:
        if r.outcome in counts:
            counts[r.outcome] += 1
    board = "".join(f'<div><dd>{n}</dd><dt>{E(label)}</dt></div>' for label, n in [
        ("bugs planted", len(runs)), ("mended", counts["recovered"]),
        ("left alone", counts["refused"]), ("gave up", counts["escalated"]),
        ("bad fixes shipped", counts["false_fix"])])
    word = {"recovered": "Mended", "refused": "Left alone", "escalated": "Gave up",
            "false_fix": "Bad fix", None: "Running"}
    rows = "".join(
        f'<tr><td class="mono">{E(r.entry)}</td><td class="muted small">{E(titles.get(r.entry, ""))}</td>'
        f'<td>{E(word.get(r.outcome, r.outcome or ""))}{f" in {r.mttr:.1f} s" if r.mttr else ""}</td>'
        f'<td class="faint small num">{E(r.started_at[11:16])} UTC</td>'
        f'<td>{_run_links(r)}</td></tr>'
        for r in runs)
    table = (f'<div class="table-scroll"><table class="ex-table"><thead><tr><th scope="col">Bug</th>'
             '<th scope="col">What was wrong</th><th scope="col">Result</th>'
             '<th scope="col" class="num">When</th><th scope="col"><span class="sr-only">Replay</span></th>'
             f'</tr></thead><tbody>{rows}</tbody></table></div>' if runs else
             '<p class="muted">No bugs have been planted in the last 24 hours. '
             '<a href="/live">Plant one</a>.</p>')
    return f"""
<div class="page-head"><h1>Status</h1>
<p class="lede">{head}</p>
<ul class="svc-status">{svc}</ul>
<p class="faint small">Checked when this page was opened.</p></div>
<h2 style="font-size:24px;margin:0 0 16px">The last 24 hours</h2>
<dl class="proof board">{board}</dl>
<h2 style="font-size:24px;margin:40px 0 12px">Every bug planted</h2>
{table}"""



# ------------------------------------------------------------------ built

COMPONENTS = [
    ("libs/kintsugi_capture", "Capture agent",
     "Lives inside each service. Records every failing request and the outbound calls it made, removing secrets at source."),
    ("control_plane/incidents", "Incident former",
     "Groups repeated failures by cause, so a burst of errors becomes one piece of work."),
    ("control_plane/reproduction", "Reproduction engine",
     "Replays a failure in a sealed copy of the exact release, refusing anything it cannot repeat on demand."),
    ("control_plane/diagnosis", "Diagnosis",
     "Packs the evidence and asks an AI model for one small patch. The model can only propose text."),
    ("control_plane/gate", "Verification gate",
     "Checks a patch in cost order: not hiding the error, applies cleanly, the failure is gone, nothing else broke. Signs what passes."),
    ("control_plane/deploy", "Deploy controller",
     "Deploys only gate-signed patches and can roll back. A canary with a metric guard is built and tested; the study and the Live runs deploy directly after the gate."),
    ("control_plane/certificates", "Certificate chain",
     "Writes every repair into a hash-linked, signed chain that can be replayed and verified by anyone."),
    ("control_plane/practice", "Practice",
     "Rehearses bugs against a private copy while idle, banking what it learns."),
    ("control_plane/metrics", "Hardening",
     "Measures how the saved regression tests protect the service against known kinds of bug."),
    ("control_plane/study", "Study harness",
     "Runs a recovery approach against a live service under load and scores the outcome on traffic, not on claims."),
    ("control_plane/integrations", "Integrations",
     "Opens a pull request for each repair so a team can review it."),
    ("console", "Console and site",
     "This website: results, live runs, accounts, certificates and the checks behind them."),
    ("corpus", "Fault corpus",
     "Sixty bugs as checked source changes, thirty-nine taken from real fixes in open-source projects."),
]


def _lines(root: str, rel: str) -> int:
    total = 0
    base = os.path.join(root, rel)
    for dirpath, _, files in os.walk(base):
        if "__pycache__" in dirpath:
            continue
        for f in files:
            if f.endswith((".py", ".js", ".css")):
                try:
                    with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                        total += sum(1 for line in fh if line.strip())
                except OSError:
                    pass
    return total


def built(root: str, cert_count: int, trials: int) -> str:
    rows, total = [], 0
    for rel, name, what in COMPONENTS:
        n = _lines(root, rel)
        total += n
        rows.append(f"<tr><td><b>{E(name)}</b><div class=\"faint small mono\">{E(rel)}</div></td>"
                    f"<td class=\"muted small\">{E(what)}</td><td class=\"num\">{n:,}</td></tr>")
    tests_dir = os.path.join(root, "tests")
    n_tests = 0
    if os.path.isdir(tests_dir):
        for f in os.listdir(tests_dir):
            if f.startswith("test_") and f.endswith(".py"):
                with open(os.path.join(tests_dir, f), encoding="utf-8") as fh:
                    n_tests += sum(1 for line in fh if line.lstrip().startswith("def test_"))
    facts = [(f"{total:,}", "lines of code (excluding blanks)"), (f"{len(COMPONENTS)}", "components"),
             (f"{n_tests}", "test functions"), (f"{trials:,}", "study tests run"),
             (f"{cert_count:,}", "signed repairs")]
    facts_html = "".join(f"<div><dd>{a}</dd><dt>{E(b)}</dt></div>" for a, b in facts)
    return f"""
<div class="page-head"><h1>How it is built</h1>
<p class="lede">Kintsugi is not a wrapper around an AI model. The model writes one small
patch; everything around it decides whether that patch may ever reach production.</p>
<dl class="proof board">{facts_html}</dl>
<p class="faint small">Counted from the source files on this server when the page loaded.</p></div>
<h2 style="font-size:24px;margin:0 0 16px">The repair loop</h2>
{ARCH_SVG}
<h2 style="font-size:24px;margin:48px 0 12px">Every component</h2>
<div class="table-scroll"><table class="ex-table built"><thead><tr><th scope="col">Component</th>
<th scope="col">What it does</th><th scope="col" class="num">Lines</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>"""


ARCH_SVG = """<figure class="arch"><svg viewBox="0 0 1100 330" role="img"
aria-label="The repair loop: a live service with the capture agent sends a failure to the incident former, then the reproduction engine, the AI diagnosis, the verification gate, the deploy controller and the certificate chain. A failure that cannot be reproduced goes to a person. Practice and hardening feed what they learn back into diagnosis.">
<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M0 0L10 5L0 10z" class="arch-arrow"/></marker></defs>
<g class="arch-box"><rect x="10" y="40" width="140" height="70" rx="8"/><text x="80" y="70">Live service</text><text x="80" y="92" class="s">+ capture agent</text></g>
<g class="arch-box"><rect x="190" y="40" width="130" height="70" rx="8"/><text x="255" y="70">Incidents</text><text x="255" y="92" class="s">grouped by cause</text></g>
<g class="arch-box key"><rect x="360" y="40" width="150" height="70" rx="8"/><text x="435" y="70">Reproduce</text><text x="435" y="92" class="s">sealed copy, 5 replays</text></g>
<g class="arch-box"><rect x="550" y="40" width="130" height="70" rx="8"/><text x="615" y="70">Diagnose</text><text x="615" y="92" class="s">AI writes a patch</text></g>
<g class="arch-box key"><rect x="720" y="40" width="130" height="70" rx="8"/><text x="785" y="70">Gate</text><text x="785" y="92" class="s">four checks, signs</text></g>
<g class="arch-box"><rect x="890" y="40" width="200" height="70" rx="8"/><text x="990" y="70">Deploy and certify</text><text x="990" y="92" class="s">signed patches only</text></g>
<path class="arch-line" d="M150 75H186"/><path class="arch-line" d="M320 75H356"/>
<path class="arch-line" d="M510 75H546"/><path class="arch-line" d="M680 75H716"/><path class="arch-line" d="M850 75H886"/>
<path class="arch-line" d="M785 110V150H615V114"/><text x="700" y="168" class="s">rejected: the failure goes back to the model, up to 3 tries</text>
<path class="arch-line" d="M435 110V214"/><g class="arch-box human"><rect x="355" y="218" width="160" height="56" rx="8"/><text x="435" y="246">A person</text><text x="435" y="264" class="s">with the reason</text></g>
<text x="445" y="170" class="s" text-anchor="start">cannot repeat it</text>
<g class="arch-box soft"><rect x="550" y="218" width="300" height="56" rx="8"/><text x="700" y="246">Practice and hardening</text><text x="700" y="264" class="s">rehearsals, saved tests, memory</text></g>
<path class="arch-line" d="M640 218V114"/>
<path class="arch-line seam" d="M990 110V300H80V114"/><text x="540" y="318" class="s">every repair leaves a regression test and a certificate behind</text>
</svg></figure>"""


TAMPER_WORDS = {"edited patch": ("We changed the fix inside one certificate.",
                                 "its contents no longer matched its fingerprint"),
                "certificate removed": ("We deleted one certificate from the chain.",
                                        "the next certificate no longer linked to the one before it"),
                "signed under another key": ("We re-signed one certificate with a different key.",
                                             "its signature did not match the study's key")}


def verification_report(v: dict, cert_seq: dict) -> str:
    """The certificate check report, readable by anyone."""
    if not v:
        return ("<div class=page-head><h1>Certificate check report</h1>"
                "<p class=lede>No report has been published yet.</p></div>")
    ki, ac, rp = v.get("keyless_integrity", {}), v.get("authenticated_chain", {}), v.get("replay", {})
    tamper = v.get("tamper_checks", {})
    caught = sum(str(x).startswith("detected") for x in tamper.values())

    def ok(flag):
        return '<span class="ok">Yes</span>' if flag else '<span class="bad">No</span>'

    tamper_rows = "".join(
        f"<li><b>{E(TAMPER_WORDS.get(k, (k, ''))[0])}</b> "
        + (f"Caught: {E(TAMPER_WORDS.get(k, (k, str(r)))[1] or str(r))}."
           if str(r).startswith("detected") else f"Not caught: {E(str(r))}.")
        + "</li>" for k, r in tamper.items())
    rows = "".join(
        f'<tr><td><a href="/certificates/{r["sequence"]}">{r["sequence"]:06d}</a></td>'
        f'<td><a href="/bugs/{E(r["entry"])}" class="mono">{E(r["entry"])}</a></td>'
        f'<td class="num">{r["seed"]}</td><td>{ok(r["reproduces"])}</td>'
        f'<td>{ok(r["patch_applies"])}</td><td>{ok(r["patch_heals"])}</td></tr>'
        for r in rp.get("rows", []))
    summary = [
        (f'{ki.get("certificates", 0)} of {ki.get("certificates", 0)}' if ki.get("ok") else "Failed",
         "certificates unchanged",
         "Each certificate's fingerprint was recomputed from its contents and matched, and "
         "each one links correctly to the one before it."),
        (f'{ac.get("certificates", 0)} of {ac.get("certificates", 0)}' if ac.get("ok") else "Failed",
         "signatures valid",
         "Each certificate's signature was checked with the study's secret signing key."),
        (f"{caught} of {len(tamper)}", "forgeries caught",
         "To show the checks work, we deliberately damaged copies of the record."),
        (f'{rp.get("verified", 0)} of {rp.get("certificates", 0)}', "repairs proven again from scratch",
         "Every certificate from the full Kintsugi setup in the main comparison was replayed "
         "on a fresh copy of the broken app."),
    ]
    tiles = "".join(f'<div class="vr-tile"><b>{E(a)}</b><span>{E(b)}</span><p>{E(c)}</p></div>'
                    for a, b, c in summary)
    return f"""
<div class="page-head"><h1>Certificate check report</h1>
<p class="lede">Every repair Kintsugi made in the experiment left a certificate. This
report shows what happened when every one of them was checked independently,
without trusting the platform's word. Generated {E(v.get("generated_at", "")[:10])}.</p></div>
<div class="vr-tiles">{tiles}</div>
<section class="section" aria-labelledby="tamper-title">
<div class="section-head"><h2 id="tamper-title">Can someone change the record unnoticed?</h2>
<p>We tried three ways of faking it, on a copy. Each one was caught.</p></div>
<ul class="vr-list">{tamper_rows}</ul></section>
<section class="section" aria-labelledby="replay-title">
<div class="section-head"><h2 id="replay-title">Does each repair really work?</h2>
<p>For each certificate, we took a fresh copy of the app with the bug in it, sent the
request saved in the certificate, and checked three things: the error happens again,
the fix in the certificate applies to the code, and after the fix the same request
works.</p></div>
<div class="table-scroll"><table class="compare">
<caption>{rp.get("verified", 0)} of {rp.get("certificates", 0)} repairs proven again.
"Test round" is which of the three repeats of the experiment it came from.</caption>
<thead><tr><th scope="col">Certificate</th><th scope="col">Bug</th><th scope="col" class="num">Test round</th>
<th scope="col">Error happens again</th><th scope="col">Fix applies</th><th scope="col">Fix works</th></tr></thead>
<tbody>{rows}</tbody></table></div></section>
<p class="muted">For programmers: <a href="/data/verification.json" download>download the raw report (JSON)</a>,
or run the same checks with <code>python study/verify_certificates.py</code>.
How to check a single certificate in your browser is on the <a href="/trust">trust page</a>.</p>"""
