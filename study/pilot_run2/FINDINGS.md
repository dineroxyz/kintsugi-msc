# Pilot run 2 findings (2026-08-19, commit 5ff8237)

Refusals matched the pre-labelled five exactly (F02, F06, F09, F11, F16),
C2 recovered 21/21 with no false fixes, C1 21/21 at median 1.16s.

The one C3 "false_fix" (F21) was a labelling defect in the harness, not a
gate failure: the beta type-drift fault fans out into several distinct
alpha incidents (POST /orders crashed first under the healthy load, GET
/quote separately). The platform correctly healed the first incident with
a gate-verified, certificate-backed patch (TypeError@main.py:72), and the
trial probe then measured the second, unhealed incident.

Fix: heal arms now respond to successive distinct incidents until the
probe passes (amendment A1 in the pre-registration); an incident
recurring after its own patch is the false-fix signal. Verified by
tests/test_runner.py::test_c3_heals_fanout_incidents_until_traffic_is_clean.
