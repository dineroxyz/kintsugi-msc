"""Two-rater patch-correctness sample (pre-registration, 'Analysis plan').

Draws a reproducible, blinded sample of admitted patches from the study's
certificates and lays each one beside the corpus ground-truth fix, so two
raters can independently judge whether the platform's patch is a correct
fix. The arm and entry id are hidden behind a random blind code, and the
sample order is shuffled with the registered seed, so a rater cannot infer
which approach produced a patch.

    python study/correctness_sample.py study/main [--n 12]

Writes to study/main/correctness/:
  sheet.md            one blinded item per patch: the failure, the platform's
                      patch, the ground-truth fix, a blank verdict line
  scores_rater_A.csv  } identical templates; each rater fills 'verdict'
  scores_rater_B.csv  } (correct | incorrect | partial) and optional notes
  key.json            blind code -> (entry, arm, seed), for after scoring

Then, once both CSVs are filled:

    python study/correctness_sample.py study/main --score
      -> agreement, Cohen's kappa, and the resolved per-item table
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from corpus import load_entry
from corpus.injector import fix_diff, inject

SERVICES = os.path.join(ROOT, "services")
SEED = 259074235          # the registered seed (candidate's matric number)
VERDICTS = {"correct", "incorrect", "partial"}


def certs_by_hash(study_dir: str) -> dict:
    cert_dir = os.path.join(study_dir, "certificates")
    out = {}
    for path in sorted(glob.glob(os.path.join(cert_dir, "*.json"))):
        cert = json.load(open(path))
        out[cert["hash"]] = cert
    return out


def build(study_dir: str, n: int) -> None:
    out = os.path.join(study_dir, "correctness")
    os.makedirs(out, exist_ok=True)

    # Scope: the gated arm's admitted patches, which are certificate-backed
    # so the exact deployed diff is recoverable with cryptographic provenance
    # (the ungated arm does not persist its diffs). Each such trial records
    # the hash(es) of the certificate(s) it issued.
    trials = [json.loads(l)
              for l in open(os.path.join(study_dir, "trials.jsonl"))]
    admitted = [t for t in trials
                if t["arm"] == "C3" and t["outcome"] == "recovered"
                and t.get("detail", {}).get("certificates")]
    if not admitted:
        print("no certificate-backed admitted patches in this study yet")
        return

    certs = certs_by_hash(study_dir)
    rng = random.Random(SEED)
    rng.shuffle(admitted)
    sample = admitted[:max(n, min(10, len(admitted)))]

    key = {}
    items = []
    for i, trial in enumerate(sample):
        code = f"P{rng.randrange(1000, 9999)}"
        entry = load_entry(trial["entry"])
        # the first certificate this trial issued carries the admitted patch
        cert = certs.get(trial["detail"]["certificates"][0])
        patch = cert["patch"] if cert else "(certificate not found)"
        faulty = inject(entry, SERVICES,
                        os.path.join(out, f"_gt_{trial['entry']}_{i}"))
        ground_truth = fix_diff(entry, faulty)
        key[code] = {"entry": trial["entry"], "arm": trial["arm"],
                     "seed": trial.get("seed"), "variant": trial.get("variant")}
        items.append({"code": code, "entry": entry, "trial": trial,
                      "patch": patch, "ground_truth": ground_truth})

    # blinded scoring sheet
    lines = ["# Patch correctness — blinded scoring sheet", "",
             "For each item, compare the PLATFORM PATCH against the "
             "GROUND-TRUTH FIX and decide whether the platform patch is a "
             "correct fix for the described failure. Record one verdict per "
             "item in your scores CSV:", "",
             "- **correct** — fixes the failure and introduces no new fault "
             "(semantically equivalent to, or better than, the ground truth)",
             "- **partial** — addresses the failure but is incomplete or "
             "narrower than the ground truth",
             "- **incorrect** — does not fix the failure, or breaks something "
             "else", "",
             "You are not told which approach produced each patch. Judge on "
             "the code alone.", "", "---", ""]
    for item in items:
        cap = item["trial"]
        req = item["entry"].trigger
        lines += [f"## {item['code']}", "",
                  f"**Failure:** {item['entry'].expected_exception} on "
                  f"`{req['method']} {req['path']}`  \n"
                  f"**What the service was asked:** "
                  f"{item['entry'].title}", "",
                  "**Platform patch:**", "```diff", item["patch"].rstrip(),
                  "```", "", "**Ground-truth fix (reference):**", "```diff",
                  item["ground_truth"].rstrip(), "```", "",
                  "**Your verdict:** `______`  (correct | partial | incorrect)",
                  "", "---", ""]
    with open(os.path.join(out, "sheet.md"), "w") as fh:
        fh.write("\n".join(lines))

    for rater in ("A", "B"):
        path = os.path.join(out, f"scores_rater_{rater}.csv")
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["code", "verdict", "notes"])
            for item in items:
                w.writerow([item["code"], "", ""])

    with open(os.path.join(out, "key.json"), "w") as fh:
        json.dump(key, fh, indent=2)

    # clean the temp ground-truth trees
    for stale in glob.glob(os.path.join(out, "_gt_*")):
        import shutil
        shutil.rmtree(stale, ignore_errors=True)

    print(f"wrote {len(items)}-item blinded sample to {out}/")
    print("  sheet.md, scores_rater_A.csv, scores_rater_B.csv, key.json")
    print("Fill both CSVs, then rerun with --score")


def _read_scores(path: str) -> dict:
    rows = {}
    with open(path) as fh:
        for row in csv.DictReader(fh):
            v = (row["verdict"] or "").strip().lower()
            if v:
                rows[row["code"]] = v
    return rows


def score(study_dir: str) -> None:
    out = os.path.join(study_dir, "correctness")
    a = _read_scores(os.path.join(out, "scores_rater_A.csv"))
    b = _read_scores(os.path.join(out, "scores_rater_B.csv"))
    codes = sorted(set(a) & set(b))
    if not codes:
        print("both CSVs must be filled for the same items first")
        return
    for label, scores in (("A", a), ("B", b)):
        bad = {c: v for c, v in scores.items() if v not in VERDICTS}
        if bad:
            print(f"rater {label} has invalid verdicts: {bad}")
            return

    agree = sum(1 for c in codes if a[c] == b[c])
    po = agree / len(codes)
    cats = sorted(VERDICTS)
    marg_a = {v: sum(1 for c in codes if a[c] == v) / len(codes) for v in cats}
    marg_b = {v: sum(1 for c in codes if b[c] == v) / len(codes) for v in cats}
    pe = sum(marg_a[v] * marg_b[v] for v in cats)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0

    key = json.load(open(os.path.join(out, "key.json")))
    # correctness rate uses the agreed verdict; disagreements flagged
    resolved = []
    for c in codes:
        agreed = a[c] if a[c] == b[c] else "DISAGREE"
        resolved.append({"code": c, "rater_A": a[c], "rater_B": b[c],
                         "agreed": agreed, **key.get(c, {})})
    correct = sum(1 for r in resolved if r["agreed"] == "correct")

    result = {"n": len(codes), "observed_agreement": round(po, 3),
              "cohens_kappa": round(kappa, 3),
              "agreed_correct": correct,
              "correct_rate_agreed": round(correct / len(codes), 3),
              "disagreements": sum(1 for r in resolved
                                   if r["agreed"] == "DISAGREE"),
              "items": resolved}
    with open(os.path.join(out, "correctness_result.json"), "w") as fh:
        json.dump(result, fh, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != "items"},
                     indent=2))
    print("per-item table + key written to correctness_result.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("study_dir")
    parser.add_argument("--n", type=int, default=12)
    parser.add_argument("--score", action="store_true")
    args = parser.parse_args()
    if args.score:
        score(args.study_dir)
    else:
        build(args.study_dir, args.n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
