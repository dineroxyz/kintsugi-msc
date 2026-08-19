# Pilot run 1 findings (2026-08-19, commit 635b3b8)

The pilot did its job: it found one harness-level defect.

Deploying a fault into beta restarts beta; while beta is down, alpha's
outbound pricing calls raised unhandled connection errors, and those
crashes won the race to be the "first incident" the arms responded to.
Consequences in this run's data:

- F20 x C3 and F21 x C3 were refused ("replay attempted an outbound call
  the capture did not record") because the heal targeted the junk
  connection-error capture, not the injected fault. The gate REFUSING a
  junk capture is correct behaviour; the harness handing it the wrong
  incident was the defect.
- F21 x C2 (the run's only false fix) patched alpha three times for that
  same wrong incident and shipped unverified, which the probe then caught.

Fix: clean alpha now maps dependency connection failures to HTTP 503
(consumer hygiene: a dead dependency is not a crash). Corpus contexts
F07/F09/F17 regenerated accordingly. Pilot rerun from the fixed harness
lives in study/pilot. Per the pre-registration, no pilot data enters the
main analysis either way.
