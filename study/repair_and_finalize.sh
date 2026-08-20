#!/usr/bin/env bash
# Post-study finalisation. Run ONLY after study/run_main.py has exited.
#
# 1. Re-runs any trial recorded as a harness_error (transient contention,
#    e.g. a subprocess that did not boot in time), by removing those rows
#    and letting the resumable runner fill the gap.
# 2. Regenerates the analysis, figures and correctness sample.
# 3. Sweeps leftover per-trial temp trees.
#
# Refuses to run while the study is still going, so it can never race the
# writer appending to trials.jsonl.
set -euo pipefail
cd "$(dirname "$0")/.."

if pgrep -f run_main.py >/dev/null; then
  echo "run_main.py is still running; wait for it to finish first." >&2
  exit 1
fi

TRIALS=study/main/trials.jsonl
ERRORS=$(grep -c harness_error "$TRIALS" 2>/dev/null || echo 0)
echo "harness_error rows: $ERRORS"

if [ "$ERRORS" -gt 0 ]; then
  echo "removing errored rows and re-running them..."
  cp "$TRIALS" "study/main/trials.pre-repair.jsonl"     # keep the raw record
  grep -v '"outcome": "harness_error"' "study/main/trials.pre-repair.jsonl" > "$TRIALS"
  # resume: fills exactly the removed (entry,arm,seed,variant) tuples
  .venv/bin/python study/run_main.py --seeds 1,2,3 --arms C0,C1,C2,C3 --ablations
fi

echo "re-running analysis, figures, correctness sample..."
.venv/bin/python study/analyze.py study/main
.venv/bin/python study/figures.py study/main
.venv/bin/python study/correctness_sample.py study/main --n 12 || true

echo "sweeping leftover temp trees..."
find "${TMPDIR:-/tmp}" -maxdepth 1 -name 'main-*' -exec rm -rf {} + 2>/dev/null || true

echo "done. Final trial count: $(wc -l < "$TRIALS")"
echo
echo "Next, to show the main-study results on the live console."
echo "The console's verifier key MUST match the key the study certificates"
echo "were signed with, or the chain reads as forged:"
echo "  git add study/main && git commit -m 'Main study results' && git push"
echo "  railway variables set \\"
echo "    KINTSUGI_STUDY_DIR=/app/study/main \\"
echo "    KINTSUGI_CERT_DIR=/app/study/main/certificates \\"
echo "    KINTSUGI_GATE_KEY=\$(cat study/main/STUDY_GATE_KEY) --service console"
echo "  railway up --service console --detach"
