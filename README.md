# Kintsugi

An AI-native platform for autonomous software repair gated on deterministic
failure reproduction. MSc project, Department of Computer Sciences, University
of Lagos.

**Author:** Abe Oluwaseyi Elizabeth<br>
**Supervisor:** Dr A.U Rufai

The platform manages instrumented demo services. When one fails, Kintsugi
captures the incident, reproduces it deterministically in an isolated shadow
environment, has a language model propose a patch, verifies the patch against
the reproduction plus the full suite plus a structural check, releases it
behind a metric guard, and rolls back on any breach. Every recovery, deployed
or refused, is issued as a tamper-evident, replayable repair certificate.

## Live

- Operations console: https://console-production-f3b9.up.railway.app
- Managed demo services: alpha and beta (JSON APIs the console manages;
  their `/health` endpoints report status)

## Invariant

No change reaches a managed service without a deterministic reproduction that
fails before it and passes after it, a non-regressed suite, and a structurally
sane diff, all recorded in a replayable certificate. Enforced by construction:
the deploy controller accepts only gate-signed verification records.

## Layout

    control_plane/   capture intake, incident formation, reproduction engine,
                     diagnosis, verification gate, guarded deploy, certificates,
                     self-practice, hardening metrics
    services/        managed demo services (alpha orders, beta pricing) — the
                     CLEAN release; study faults are injected into copies
    console/         operations console + control-plane API (FastAPI, server
                     rendered, no build step; API mounted at /api)
    corpus/          fault corpus (60 entries) + miner + injector. 21 seeded,
                     39 transplanted from real OSS fix commits with provenance
    study/           pre-registration, resumable study runner, analysis,
                     figures, two-rater correctness sampler
    tests/           platform test suite (170 tests)

## Running

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python -m pytest tests/ -q                 # full suite

    python corpus/generate_seeded.py           # regenerate corpus from source
    python corpus/generate_mined.py            # (each owns its id prefix)
    python corpus/generate_mined2.py

    python study/run_main.py --seeds 1,2,3 --arms C0,C1,C2,C3 --ablations
    python study/analyze.py study/main         # pre-registered statistics
    python study/figures.py study/main         # greyscale figures (PNG + PDF)

The study runner is resumable: rerun the same command after an interruption
and it continues from the last recorded trial. The corpus is frozen for the
study at the git tag `corpus-v1-frozen`.

## Configurations compared

    C0   no recovery (baseline)
    C1   restart, then roll back to the previous release
    C2   generative repair, admitted on the suite alone (ungated)
    C3   generative repair behind the reproduction gate (this project)
