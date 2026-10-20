# Study R: Kintsugi against a real application (pre-registered)

Registered before any classification, reproduction or gate trial of this study
was run. Only the candidate list (`candidates.jsonl`, produced by `mine.py`
from the subject's public git history) existed when this was committed. No
language model is called anywhere in this study.

## Purpose

The main study managed two small services written for it. This study asks
whether the platform's mechanism holds on a real, independently developed,
widely deployed application, measured on that application's own real
defects and its own real test suite.

## Subject

Mealie (github.com/mealie-recipes/mealie), a self-hosted recipe manager:
FastAPI, SQLAlchemy over SQLite, about 43,000 lines of Python, 2,170
integration tests plus unit tests, 5,690 commits. The current release (HEAD
of the default branch on the day of registration) is the clean release.

## Defects

Every commit whose subject starts with "fix" and which changes both the
`mealie/` package and `tests/` is a candidate. A candidate is usable only if
the reverse of its application-side change applies cleanly to the clean
release; applying that reverse re-introduces the original defect into
today's code. The commit's own test changes are the discriminating tests.
No candidate is excluded or labelled by hand: every step below is
mechanical, and every candidate is reported.

## Steps and outcomes, per candidate

1. **Ground truth.** The discriminating tests are run on the clean release
   and on the faulty release. The candidate is *valid* if they pass on the
   clean release and fail on the faulty one.
2. **Kind of failure.** With the capture agent installed in the running
   application, a valid candidate is a *runtime failure* if running its
   discriminating tests against the faulty release raises an unhandled
   exception that the clean release does not; otherwise it is a *wrong
   output* defect, which is outside the platform's scope (it detects
   failures, not wrong answers).
3. **Reproduction.** For each runtime failure, the first captured failure is
   replayed five times, each in a fresh process against the faulty
   release, under two modes:
   - **request only**: the platform as designed in Chapter Three, with a
     fresh database and the captured request (credentials re-issued for
     the captured principal);
   - **request + state snapshot**: as above, but starting from a copy of
     the application's database taken immediately before the failing
     request was handled (an extension built for this study).
   The verdict rules are those of the reproduction engine: reproduced only
   if all five replays fail with the same signature as the capture.
4. **Gate on the real fix.** The developers' own fix, as a patch against
   the faulty release, is put through the three gate conditions, each
   evaluated independently: structural check; reproduction (snapshot mode;
   passes if the replay no longer fails); and the application's own unit
   and integration suites *with the fix's own new tests removed*, so the
   suite is the one the developers had before they knew of the defect.
5. **Gate on cheat patches.** Two mechanical cheats are generated at the
   failure's deepest application frame and put through the same three
   conditions: **S1** catches the captured exception type and returns
   None; **S2** catches it and raises an HTTP 400 error instead. Each is
   then also checked against the discriminating tests, which stand in for
   the ground-truth correctness check.

## Reported

Counts at each step; the reproduction rate under each mode, with the
reasons for every refusal; for the real fixes, how many each condition
admits; for each cheat, which conditions reject it and how many the gate
as a whole would admit, and of those how many the discriminating tests
show to be wrong. No hypothesis test is planned: this is a measurement on
one application, reported in full.

## Known limits, stated in advance

One application; defects transplanted onto today's code rather than
observed in production; the load is the developers' own tests, not user
traffic; the snapshot covers the database only, not files on disk; binary
request bodies are captured as text.

## Amendment R2: more applications (registered before any trial on them)

Registered on 2026-10-20 after the Mealie results, before any classification
trial on the applications below. The defect mining (same rule as above, with
each project's own test folder) had been run; nothing else.

Subjects: Prefect (workflow orchestration server), lnbits (Lightning wallet
server), Langflow (AI workflow builder) and Frigate (video recorder), at the
HEAD of each default branch on the registration date (recorded in
SUBJECTS.txt). Open WebUI and the full-stack FastAPI template were also mined
but yielded no candidate whose fix came with tests, so they are reported as
contributing zero.

Steps 1 and 2 (ground truth and kind of failure) are run for every candidate
of every subject. Because each application builds its web app differently,
the capture agent is attached generically: a pytest plugin wraps every
Starlette/FastAPI application's middleware stack when it is built, so any
unhandled exception that leaves the application is captured, whatever code
created the app. Steps 3 to 5 (reproduction, gate on the real fix, gate on
the cheats) are run for the runtime failures of any subject for which a
replay adapter can be written; for a subject where none can be, its runtime
failures are reported as found and not reproduced, with the reason. Each
subject's runtime-failure share, with its count, is the primary result;
there is no pooled hypothesis test.

Note added at commit, before the full runs: while making the harness work,
step 1-2 was run on three candidates each of lnbits and Prefect and on two
Mealie candidates (one being Mealie's known runtime failure, used to check
that the generic capture detects it; the first version of the plugin did not,
because FastAPI overrides the method it hooked, and was corrected). Those
harness-check results are discarded; every candidate is run afresh.
