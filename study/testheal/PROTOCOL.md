# Study T: healing real defects from a failing test (pre-registered)

Registered before any trial of this study.

## Question
Kintsugi's reproduction so far comes from a captured crashing request, and on
four real applications only 8 of 241 real defects crashed. Teams find most
defects through failing tests. Taking a failing test as the reproduction, how
many real defects does Kintsugi repair with proof?

## Subjects and sample
The valid defects of Study R and amendment R2 (Mealie, lnbits, Prefect,
Langflow), each re-introduced into the application's current release. 15 per
application are drawn with the registered seed 259074235 (all of lnbits' 19
are eligible), 60 in total.

## Per defect
1. Reproduction: the defect's discriminating tests are run five times, each in
   a fresh process. Reproduced only if all five fail with the same set of
   failing tests.
2. Localisation: the model (claude-opus-5) is shown the failing tests' output
   and source and a list of candidate files (application files in the failure
   traceback and modules the test file imports) and picks at most three.
3. Repair: the model is shown those files and writes a unified diff. Up to
   three attempts; each rejection's reason is fed to the next.
4. Gate, each condition must pass: structural check; security check (scope =
   the files shown to the model; added-line limit 60 for this mode); the patch
   applies; the discriminating tests pass on five of five fresh runs; every
   test in the discriminating tests' folders passes.
5. The developers' fix is never shown to the model.

## Measures
- reproduced: share of the sample reproduced at step 1
- healed: share of the sample with an admitted patch
- per application, with Wilson 95 per cent intervals
- cost per healed defect (model usage at conservative prices)
The study stops when the spend cap of 40 US dollars (at conservative prices)
is reached; defects not reached are reported as not run.

## Limits stated in advance
The population was mined for defects that came with tests, so it measures the
defects a test catches, not all defects. The discriminating tests are both
the reproduction and part of the gate's evidence; the folder suite guards
against breaking other behaviour. One model; three attempts.

Note at commit: the Langflow stage-1 run was still in progress, so its 15 are drawn from the 107 Langflow defects classified as valid at that time. The drawn sample is frozen in sample.json, committed with this note before any trial.

Harness checks before the run (one defect, three times, results discarded) spent 10.70 dollars at conservative prices (five checks); the cap for the run is reduced to 29.30 so the total stays within 40. Changes from those checks: retries for network errors, focused file excerpts, capped model thinking, and showing the model a candidate file it edits without having seen it.
