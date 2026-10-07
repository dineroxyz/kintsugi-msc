# Kintsugi viva demo runbook

About twelve minutes. Every segment is a real run from the repository root
(`.venv` active, `.env` present). Fallback videos of every segment are in
`~/Desktop/kintsugi-demo/`; play one only if the live run misbehaves.

## 0. Preflight, before the examiners arrive

```bash
source .venv/bin/activate
python -m pytest -q                     # expect 216 passed; if not, use the videos
KINTSUGI_STUDY_DIR=$PWD/study/main \
KINTSUGI_BANK_PATH=$PWD/study/supp/practice_bank.jsonl \
uvicorn console.app:app --port 8100     # console on http://localhost:8100
```

Live site, if the laptop fails: https://console-production-f3b9.up.railway.app
(add `?theme=light` for a projector).

## 1. Open on the home page and practice (2 min)
Video: `demo-1-home-and-practice.mp4`

Home page: the mended bowl, how it works in three steps, the four findings.
Then the Practice page: all 60 bugs rehearsed while idle, 45 would be fixed,
15 would be left alone, 44 regression tests saved.

Say: "Every repair leaves a test behind. Those saved tests now catch 45 of the
60 bugs before deploy, and 25 of them were caught by a test saved for a
different bug."

## 2. Live break and heal (4 min)
Video: `demo-2-break-and-heal.mp4`

```bash
python demo/segments.py heal
```

Deploys bug F03 into the live order service under load. Kintsugi captures the
failure, replays it five times in a shadow, asks the model for a patch, and
deploys only a patch that passes the gate. About 15 to 25 seconds.

Say while it runs: the gate's conditions in order of cost: not hiding the
error, applies to the code, the reproduction now passes, nothing else broke.
When attempt 1 is rejected for "applies": "this is the study's main finding
about the model in one incident: its first patch was the right fix in the
wrong format, and the retry fixed the format."

## 3. Check and replay a certificate (3 min)
Video: `demo-3-certificate-replay.mp4` and `demo-6-certificate.mp4`

Let the examiner pick any certificate on the console's Certificates page.

```bash
DEMO_CERT=0 python demo/segments.py replay     # put their pick in DEMO_CERT
```

Verifies all 494 certificates, replays the chosen one from the certificate
alone against a fresh copy of the broken service, then edits one character of
a copied certificate and shows the chain naming it as tampered.

Note: DEMO_CERT must be a certificate from the base gated runs (the replay
looks up which bug it healed). Sequence 0 always works.

## 4. A bug Kintsugi will not touch (2 min)
Video: `demo-4-race-refusal.mp4`

```bash
python demo/segments.py refusal
```

Bug F11 is a race between two requests. It cannot be made to happen on
demand, so Kintsugi refuses before any model call, with the reason attached.

Say: "It declined 45 of 45 such tests in the study, and never declined a bug
it could have fixed."

## 5. The numbers, honestly (1 min)
Video: `demo-5-results.mp4`

Results page. Say it straight:
- Restart and roll back fixed everything fastest, about a second, because
  every bug here arrived with a new version and the old one was there to go
  back to. Kintsugi is for when it is not.
- Neither AI approach shipped a bad fix, with or without the gate. The gate's
  value on this corpus is proof and correct refusal, not fewer bad fixes.
- The gate is not slower: 12.3 s gated, 12.5 s ungated.
- With memory of a past repair, the first patch was accepted 92% of the time,
  against 43% without.

## Re-recording the videos

```bash
python demo/record.py heal replay refusal        # real runs, timed transcripts
node demo/film.js ~/Desktop/kintsugi-demo        # console must be on :8100
```

`film.js` needs ffmpeg and puppeteer (set PUPPETEER to its path if it is not
installed in this project).
