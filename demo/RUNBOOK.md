# Kintsugi demo runbook — the 12-minute arc

Every segment is a runnable command from the repo root (venv active,
`.env` present). Record each segment as a fallback video BEFORE the
defence; play recordings only if the live run misbehaves.

## 0. Preflight (before the audience arrives)

```bash
source .venv/bin/activate
python -m pytest tests/ -q          # 85+ green; abort the live plan if not
uvicorn console.app:app --port 8100 # console on http://localhost:8100
```

## 1. Open on the practice log (2 min)

"While I slept, it practised." Show the console's Practice page: the
overnight bank of rehearsals with would_heal patches and banked refusals.
To regenerate a fresh bank (real model, ~2 min for 10 ticks):

```bash
python demo/practice_overnight.py --ticks 10
```

## 2. Live break-and-heal (4 min)

One command: deploys a real fault into a live service under load, the
platform captures, reproduces, diagnoses with the real model, gates,
deploys, and the previously failing request returns 200.

```bash
python - <<'EOF'
import tempfile, json
from corpus import load_entry
from control_plane.study.runner import run_trial
from control_plane.certificates.issuer import CertificateStore
store = CertificateStore("study/demo_certs")
trial = run_trial(load_entry("F01"), "C3", tempfile.mkdtemp(),
                  certificates=store)
print(json.dumps(trial.to_dict(), indent=2))
EOF
```

Talking point while it runs (~25s): the gate's three conditions, in cost
order. Show MTTR in the output.

## 3. Examiner-picked certificate replay (3 min)

Let the examiner choose any certificate from the console. Then prove it
from the certificate alone against a freshly injected faulty tree:

```bash
python - <<'EOF'
import json, sys, tempfile, os
from control_plane.certificates.issuer import CertificateStore, replay_certificate
from corpus import load_entry
from corpus.injector import inject
store = CertificateStore("study/pilot/certificates")
cert = store.verify_chain()[0]        # or the examiner's pick by sequence
entry = load_entry("F01")             # the entry the picked certificate healed
fresh = inject(entry, "services", tempfile.mkdtemp() + "/tree")
print(json.dumps(replay_certificate(cert, fresh), indent=2))
EOF
```

Then the tamper demo: edit one character of a certificate file, reload
the console's Certificates page, watch the chain break and name the
sequence number.

## 4. Live race-condition refusal (2 min)

The platform declining work it cannot verify, with a stated reason:

```bash
python - <<'EOF'
import tempfile, json
from corpus import load_entry
from control_plane.study.runner import run_trial
trial = run_trial(load_entry("F11"), "C3", tempfile.mkdtemp())
print(json.dumps(trial.to_dict(), indent=2))   # outcome: refused
EOF
```

Talking point: refusal is a first-class outcome; the ungated arm (C2)
would have shipped a guess here.

## 5. The numbers (1 min)

Console Overview page: the four arms side by side from the pilot —
C0 nothing, C1 fast but only rollback, C2 fast but ships false fixes,
C3 slower, refuses what it cannot prove, zero false fixes.

Live URL fallback: https://console-production-f3b9.up.railway.app

## Fallback recordings

Record segments 1–4 with `asciinema` or QuickTime before the defence;
keep them on the desktop named demo-1-practice.mov ... demo-4-refusal.mov.
