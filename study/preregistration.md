# Pre-registration: Kintsugi evaluation study

Frozen on 2026-08-19, before any pilot or main-study trial was run. The
commit that introduces this file is the registration timestamp. Changes
after this date are amendments and must be listed in the Amendments
section with their reason; results reported in the dissertation follow
the definitions below regardless of what later exploration suggests.

## Configurations (arms)

All arms run on the same platform, the same fault corpus, the same
deterministic load plan.

- **C0, no recovery.** Incidents are observed and nothing is done. The
  control that turns time to recovery into a measurable ceiling.
- **C1, fixed repertoire.** Restart the service on first sight of a
  failure signature; roll back to the previous release if the same
  signature recurs after restart; escalate when the repertoire is spent.
- **C2, ungated repair.** The repair loop runs without the reproduction
  gate: candidate patches are admitted on structural checks and the test
  suite alone, honestly recorded as such by a distinct signing mode.
- **C3, gated repair.** The full loop: deterministic reproduction or
  refusal, evidence-packed diagnosis, the three-condition gate, deploy of
  gate-signed records only.

## Trial protocol

One trial is one corpus entry under one arm: clean release r1 live with
its real dependency and the load generator running; the fault deployed
over it as release r2; the arm responds to what the capture stream shows.
Recovery is judged by the platform's probe (the entry's setup requests
followed by its trigger, none returning 5xx across five rounds) and by
the traffic stream, never by the arm's own report.

## Outcome classes (exhaustive, per trial)

- `recovered`: probe passes after the arm acted.
- `unrecovered`: observation window closed with the fault still live.
- `escalated`: the arm exhausted its options without recovery.
- `refused`: C3 declined to act because the incident did not reproduce
  deterministically (counted separately from escalation because refusal
  with a stated reason is designed behaviour under H2).
- `false_fix`: the arm deployed a patch and the probe still fails.
- `fault_never_fired`: the injection did not surface under load; the
  trial is rerun once and excluded (and reported) if it repeats.

## Metrics (frozen definitions)

- **MTTR** (seconds): from the first 5xx sample in the load generator's
  stream after fault deployment to the moment the probe first passes.
  Defined only for `recovered` trials; unrecovered trials are reported
  as censored at the observation window, not imputed.
- **Recovery rate**: `recovered` trials / all trials, per arm.
- **False-fix rate**: `false_fix` / (`false_fix` + `recovered` where a
  patch was deployed), per arm. The headline safety metric.
- **Reproduction rate** (C3 only): incidents whose reproduction was
  admitted / all incidents presented to the engine.
- **Refusal correctness** (C3 only): refusals whose corpus entry was
  pre-labelled `expected_c3 = refused` / all refusals. The corpus
  labels were fixed before any trial (commit edde921).
- **Escalation rate**: (`escalated` + `refused`) / all trials, per arm.
- **Repair cost**: model candidates consumed per healed incident, and
  wall seconds of the heal call.

## Study sizes

- **Pilot**: at least 15 corpus entries, every arm, one seed. Purpose:
  shake out the harness. Pilot data does not enter the main analysis.
- **Main study**: the full corpus at the time of the study freeze,
  every arm, three seeds (independent repetitions; the model is called
  fresh per repetition). Ablations afterwards on C3 only: evidence
  memory off, regeneration off, guard off.

## Analysis plan

Per-fault paired comparison across arms. Medians with bootstrap 95%
confidence intervals (10,000 resamples) for MTTR; Wilcoxon signed-rank
for paired MTTR differences on the faults recovered by both arms under
comparison; two-sided Fisher exact tests for rate differences
(recovery, false fix). Holm correction across the hypothesis family
H1 to H5. Effect sizes as Cliff's delta. No metric, threshold, or test
may be swapped after seeing main-study data.

Correctness of a sample of admitted patches (at least 10, or all if
fewer) is independently judged against the corpus ground-truth fix by
two raters; disagreements resolved by discussion and reported.

## Amendments

**A1 (2026-08-19, before the main study; prompted by pilot run 2).**
Protocol clarification for the heal arms (C2, C3): a trial responds to
successive DISTINCT incidents until the probe passes, up to four
incidents per trial, because one injected fault can fan out into several
consumer incidents (pilot run 2, F21: a dependency type drift broke both
alpha paths that price SKUs, and the platform correctly healed the first
incident while the trial's probe measured the second). `false_fix` now
means specifically: an incident RECURS after its own patch deployed, or
the probe fails with no new incident forming. Outcome definitions are
otherwise unchanged; no metric or statistical test changed. Pilot runs 1
and 2 are preserved under study/pilot_run1 and study/pilot_run2 with
findings notes; neither enters the main analysis, as already registered.
