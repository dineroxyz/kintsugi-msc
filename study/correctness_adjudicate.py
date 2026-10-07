"""Adjudicate the differential correctness check into the registered verdicts.

study/differential.py compares each admitted repair with the clean release
on a fixed request battery. Repairs whose only differences are error text
or the status code of an already-rejected input are correct. Every repair
with a behavioural difference was inspected by hand (fault, patch and the
differing requests); the judgement for each, with its reason, is recorded
in JUDGED below so the table can be regenerated and challenged.

    python study/correctness_adjudicate.py   -> study/correctness_summary.json
"""

from __future__ import annotations

import collections
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (study, entry) -> (verdict, reason); verdicts as registered: correct |
# partial | incorrect, with "correct (alternative)" kept visible.
JUDGED = {
    ("main", "F01"): ("incorrect", "the crash on unknown discount codes stopped, but percentage codes such as PCT15 quietly stopped giving any discount"),
    ("weaker_model", "F01"): ("incorrect", "as with the frontier model: percentage discount codes quietly stopped working"),
    ("main", "F08"): ("correct (alternative)", "a badly formed discount code is now refused with an error instead of being ignored; stricter, but a reasonable choice"),
    ("main", "F10"): ("incorrect", "the crash stopped, but the check that refuses empty orders was not put back, so empty orders are now accepted and saved"),
    ("main", "F21"): ("partial", "the two places that crashed now cope with a price sent as text, but the pricing service still sends text and checkout, a third place, now fails"),
    ("weaker_model", "F21"): ("partial", "as with the frontier model: two places fixed, checkout still fails, and the real cause in the pricing service is untouched"),
    ("main", "M16"): ("incorrect", "the quote reference no longer crashes, but it now shows the product name where the quantity should be"),
    ("weaker_model", "M16"): ("incorrect", "the quote reference now shows the product name instead of the quantity"),
    ("main", "N12"): ("partial", "a bundle listed a product that does not exist; the fix refuses or skips it, which is safe, but cannot know which product was meant"),
    ("weaker_model", "M09"): ("incorrect", "instead of refusing an unsupported currency, the fix invented an exchange rate for it"),
    ("weaker_model", "N19"): ("incorrect", "converted prices are now rounded to whole units, losing the cents"),
}


def main() -> int:
    d = json.load(open(os.path.join(ROOT, "study", "correctness_differential.json")))
    rows, table = [], collections.defaultdict(collections.Counter)
    for r in d["results"]:
        if r["verdict"] in ("same", "wording"):
            v, why = "correct", "behaves as the clean release" + (
                "; error text differs" if r["verdict"] == "wording" else "")
        elif r["verdict"] == "error_code":
            v, why = "correct", "an invalid input is still rejected, with a different 4xx status"
        elif r["verdict"] == "behaviour":
            v, why = JUDGED[(r["study"], r["entry"])]
        else:
            v, why = "not tested", r.get("error", "")
        table[r["study"]][v] += r["trials"]
        rows.append({"study": r["study"], "entry": r["entry"], "seeds": r.get("seeds"),
                     "trials": r["trials"], "check": r["verdict"], "verdict": v, "reason": why})
    out = {"by_study": {k: dict(v) for k, v in table.items()},
           "totals": {k: sum(v.values()) for k, v in table.items()}, "rows": rows}
    with open(os.path.join(ROOT, "study", "correctness_summary.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps({"by_study": out["by_study"], "totals": out["totals"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
