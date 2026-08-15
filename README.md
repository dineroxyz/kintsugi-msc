# Kintsugi

An AI-native platform for autonomous software repair gated on deterministic
failure reproduction. MSc project, Department of Computer Science, University
of Lagos.

The platform manages instrumented demo services. When one fails, Kintsugi
captures the incident, reproduces it deterministically in an isolated shadow
environment, has a language model propose a patch, verifies the patch against
the reproduction plus the full suite plus a structural check, releases it
behind a metric guard, and rolls back on any breach. Every recovery, deployed
or refused, is issued as a tamper evident, replayable repair certificate.

## Layout

    control_plane/   FastAPI control plane: capture intake, incidents,
                     reproduction engine, diagnosis, gate, deploy, certificates,
                     self practice, hardening metrics
    services/        managed demo services (alpha, beta)
    console/         operations console (Next.js)
    corpus/          fault corpus mined from OSS revision history
    study/           experiment orchestration, pre-registration, analysis
    infra/           deployment configuration
    tests/           platform test suite

## Invariant

No change reaches a managed service without a deterministic reproduction that
fails before it and passes after it, a non regressed suite, and a structurally
sane diff, all recorded in a replayable certificate. Enforced by construction:
the deploy controller accepts only gate signed verification records.
