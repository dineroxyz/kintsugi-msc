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

**A2 (2026-10-03, after the main study, before any supplementary trial
of the memory arms).** Disclosure and supplement. The registered
"evidence memory off" ablation was configured identically to full C3,
because the main study loaded no practice bank; it is reported as a
replication of C3, not as a memory test. To test H4 and H5 as registered,
a supplementary study (study/run_supp.py, results in study/supp/) runs:
one self-practice rehearsal of every frozen-corpus entry into a fresh
bank; C3 with the bank loaded at three seeds in two variants, C3-mem
(full bank: recurring classes, H4) and C3-mem-loo (the entry's own
rehearsal withheld, so only same-shape sibling rehearsals are recallable:
rehearsed-but-unseen faults, H5 clause 3), compared with main-study C3 as
the no-memory baseline; and the kill matrix of the banked regression
suite over the corpus mutant pool, from which mutation-score growth over
the rehearsal sequence and the recurrence measure are computed. Primary
H4/H5 measure: first-attempt repair success (admitted on candidate 1) per
trial, Fisher exact two-sided against main-study C3 on the 45 repairable
faults; secondary: recovery rate, MTTR (Wilcoxon, paired per fault).
Recurrence is operationalised as a previously healed fault reaching
production again when redeployed, i.e. not killed by the banked suite
before deploy. Main-study analyses are unchanged. Also disclosed: the
runner's KINTSUGI_SEED variable is read by no component, so seeds are
independent fresh repetitions, as the Study sizes section already states.

**A3 (2026-10-07, before any trial of the weaker-model study).** Motivation:
in the main study neither repair arm shipped a false fix with
claude-opus-5, so H2 could not be tested. A3 re-runs the two repair arms
with a weaker model, claude-haiku-4-5-20251001, everything else unchanged
(same frozen corpus, same platform commit for the platform code, same
probe, same three seeds, same budget of three candidates). C0 and C1 do
not call a model and are not re-run. Runner: study/run_haiku.py, results
in study/haiku/. Primary measure: false-fix rate, C2 against C3, two-sided
Fisher exact test (as registered for H2). Secondary: recovery rate on the
45 repairable faults (Fisher), refusal correctness, first-attempt
admission, MTTR (Wilcoxon, paired per fault), and the condition on which
each rejected C3 candidate failed. Prediction: with the weaker model, C2
ships some false fixes and C3 ships none. If C2 ships none, that is
reported as a finding (the gate's value does not depend on model
strength on this corpus). No other analysis is changed.
