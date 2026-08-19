"""Regenerate the seeded corpus entries against the current clean services.

The corpus is data (corpus/faults/*.json); this script is its single source
of truth for the SEEDED entries, so that when a clean service evolves the
whole corpus is regenerated and re-verified in one step rather than patched
by hand. Run it from the repo root:

    python corpus/generate_seeded.py

It refuses to write anything if any mutation's find-text is not unique in
the clean tree, in either direction.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAULTS = os.path.join(ROOT, "corpus", "faults")
SERVICES = os.path.join(ROOT, "services")

ALPHA = "alpha/main.py"
BETA = "beta/main.py"

ALPHA_IMPORTS = ("import os\nimport threading\n\nimport httpx\n"
                 "from fastapi import FastAPI, HTTPException\n")

ENTRIES = [
    {
        "id": "F01",
        "title": "Unknown discount code makes /quote multiply by None",
        "pattern": "none-from-dict-get",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "regression from a shared helper to a raw dict lookup "
                          "without default; the sentinel None flows into "
                          "arithmetic (Yuan et al. 2014's mishandled-error shape)"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "    rate = discount_rate(code)\n"
                    "    total = unit * quantity * (1 - rate)\n",
            "replace": "    rate = DISCOUNTS.get(code)\n"
                       "    total = unit * quantity * (1 - rate)\n"}],
        "trigger": {"method": "GET", "path": "/quote",
                    "params": {"sku": "WIDGET", "code": "BOGUS"}},
        "expected_exception": "TypeError",
        "expected_c3": "healed",
        "notes": "the flagship stateless fault; first real heal used this shape",
    },
    {
        "id": "F02",
        "title": "Unknown discount code crashes /orders/{id}/discounted",
        "pattern": "none-from-dict-get",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "same family as F01 but the handler reads state a "
                          "fresh replay does not carry"},
        "service": "alpha",
        "deterministic": True,
        "stateful": True,
        "mutations": [{
            "file": ALPHA,
            "find": "    rate = discount_rate(code)\n"
                    "    discounted_total = order[\"total\"] * (1 - rate)\n",
            "replace": "    rate = DISCOUNTS.get(code)\n"
                       "    discounted_total = order[\"total\"] * (1 - rate)\n"}],
        "setup": [{"method": "POST", "path": "/orders",
                   "json": {"lines": [{"sku": "WIDGET", "quantity": 2}]}}],
        "trigger": {"method": "GET", "path": "/orders/1/discounted",
                    "params": {"code": "BOGUS"}},
        "expected_exception": "TypeError",
        "expected_c3": "refused",
        "notes": "expected refusal: capture does not carry the ORDERS state, "
                 "so the replay cannot reach the captured failure",
    },
    {
        "id": "F03",
        "title": "Missing-order guard removed from /orders/{id}",
        "pattern": "guard-removal-keyerror",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "lookup guard lost in a refactor; direct indexing of "
                          "a store raises KeyError for absent keys"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "def get_order(order_id: int) -> dict:\n"
                    "    if order_id not in ORDERS:\n"
                    "        raise HTTPException(status_code=404, detail=f\"no order {order_id}\")\n"
                    "    return ORDERS[order_id]\n",
            "replace": "def get_order(order_id: int) -> dict:\n"
                       "    return ORDERS[order_id]\n"}],
        "trigger": {"method": "GET", "path": "/orders/999999"},
        "expected_exception": "KeyError",
        "expected_c3": "healed",
    },
    {
        "id": "F04",
        "title": "Upstream price field renamed out from under /quote",
        "pattern": "contract-drift-keyerror",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "consumer reads a response field the dependency "
                          "renamed; classic cross-service contract drift"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "    unit = resp.json()[\"price\"]\n",
            "replace": "    unit = resp.json()[\"unit_price\"]\n"}],
        "trigger": {"method": "GET", "path": "/quote",
                    "params": {"sku": "WIDGET"}},
        "expected_exception": "KeyError",
        "expected_c3": "healed",
        "notes": "reproduction depends on the recorded beta response pair",
    },
    {
        "id": "F05",
        "title": "Order line quantity loses its integer coercion",
        "pattern": "boundary-type-confusion",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "type coercion dropped at a JSON boundary; a string "
                          "quantity reaches arithmetic"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "        quantity = int(line.get(\"quantity\", 1))\n",
            "replace": "        quantity = line.get(\"quantity\", 1)\n"}],
        "trigger": {"method": "POST", "path": "/orders",
                    "json": {"lines": [{"sku": "WIDGET", "quantity": "2"}]}},
        "expected_exception": "TypeError",
        "expected_c3": "healed",
    },
    {
        "id": "F06",
        "title": "Order total read as attribute instead of key",
        "pattern": "dict-attribute-confusion",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "dict record accessed as an object after a model "
                          "refactor elsewhere in the codebase"},
        "service": "alpha",
        "deterministic": True,
        "stateful": True,
        "mutations": [{
            "file": ALPHA,
            "find": "    discounted_total = order[\"total\"] * (1 - rate)\n",
            "replace": "    discounted_total = order.total * (1 - rate)\n"}],
        "setup": [{"method": "POST", "path": "/orders",
                   "json": {"lines": [{"sku": "GADGET", "quantity": 1}]}}],
        "trigger": {"method": "GET", "path": "/orders/1/discounted",
                    "params": {"code": "SAVE10"}},
        "expected_exception": "AttributeError",
        "expected_c3": "refused",
        "notes": "stateful for the same reason as F02",
    },
    {
        "id": "F07",
        "title": "Quantity floor check removed, /quote divides by zero",
        "pattern": "validation-removal-divzero",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "input validation removed as 'dead code'; the "
                          "denominator it protected goes to zero"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "    if quantity < 1:\n"
                    "        raise HTTPException(status_code=422, detail=\"quantity must be at least 1\")\n"
                    "    try:\n"
                    "        resp = _pricing.get(f\"{BETA_URL}/price/{sku}\")\n",
            "replace": "    try:\n"
                       "        resp = _pricing.get(f\"{BETA_URL}/price/{sku}\")\n"}],
        "trigger": {"method": "GET", "path": "/quote",
                    "params": {"sku": "WIDGET", "quantity": 0}},
        "expected_exception": "ZeroDivisionError",
        "expected_c3": "healed",
    },
    {
        "id": "F08",
        "title": "PCT discount codes parsed without the digit guard",
        "pattern": "parse-without-guard",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "int() on user input with the isdigit guard lost; "
                          "malformed codes raise ValueError"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "    if code.startswith(\"PCT\") and code[3:].isdigit():\n",
            "replace": "    if code.startswith(\"PCT\"):\n"}],
        "trigger": {"method": "GET", "path": "/quote",
                    "params": {"sku": "SPROCKET", "code": "PCTXX"}},
        "expected_exception": "ValueError",
        "expected_c3": "healed",
    },
    {
        "id": "F09",
        "title": "Transient pricing cache miss raises at random",
        "pattern": "nondeterministic-transient",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "a transient dependency failure surfacing as an "
                          "unhandled exception on a coin flip"},
        "service": "alpha",
        "deterministic": False,
        "stateful": False,
        "mutations": [
            {"file": ALPHA,
             "find": ALPHA_IMPORTS,
             "replace": "import os\nimport random\nimport threading\n\nimport httpx\n"
                        "from fastapi import FastAPI, HTTPException\n"},
            {"file": ALPHA,
             "find": "    try:\n"
                     "        resp = _pricing.get(f\"{BETA_URL}/price/{sku}\")\n",
             "replace": "    if random.random() < 0.4:\n"
                        "        raise RuntimeError(\"pricing cache miss\")\n"
                        "    try:\n"
                        "        resp = _pricing.get(f\"{BETA_URL}/price/{sku}\")\n"}],
        "trigger": {"method": "GET", "path": "/quote",
                    "params": {"sku": "WIDGET"}},
        "expected_exception": "RuntimeError",
        "expected_c3": "refused",
        "notes": "expected refusal: the 5-of-5 determinism rule must reject it",
    },
    {
        "id": "F10",
        "title": "Empty order accepted then indexed for first_sku",
        "pattern": "guard-removal-indexerror",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "emptiness guard removed; later code indexes the "
                          "first element of the empty collection"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "    lines = order.get(\"lines\", [])\n"
                    "    if not lines:\n"
                    "        raise HTTPException(status_code=422, detail=\"order has no lines\")\n"
                    "    total = 0.0\n",
            "replace": "    lines = order.get(\"lines\", [])\n"
                       "    total = 0.0\n"}],
        "trigger": {"method": "POST", "path": "/orders", "json": {"lines": []}},
        "expected_exception": "IndexError",
        "expected_c3": "healed",
    },
    {
        "id": "F11",
        "title": "Stock reservation races between check and pop",
        "pattern": "toctou-race",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "check-then-act on shared state with the lock "
                          "dropped; two requests pass the emptiness check and "
                          "the second pops an empty list"},
        "service": "alpha",
        "deterministic": False,
        "stateful": False,
        "requires_concurrency": True,
        "mutations": [
            {"file": ALPHA,
             "find": ALPHA_IMPORTS,
             "replace": "import os\nimport threading\nimport time\n\n"
                        "from fastapi import FastAPI, HTTPException\n"},
            {"file": ALPHA,
             "find": "    with _stock_lock:\n"
                     "        if not STOCK[sku]:\n"
                     "            raise HTTPException(status_code=409, detail=f\"{sku} out of stock\")\n"
                     "        unit = STOCK[sku].pop()\n",
             "replace": "    if not STOCK[sku]:\n"
                        "        raise HTTPException(status_code=409, detail=f\"{sku} out of stock\")\n"
                        "    time.sleep(0.004)\n"
                        "    unit = STOCK[sku].pop()\n"}],
        "setup": [{"method": "POST", "path": "/restock/WIDGET",
                   "json": {"count": 4}}],
        "trigger": {"method": "POST", "path": "/reserve/WIDGET"},
        "expected_exception": "IndexError",
        "expected_c3": "refused",
        "notes": "expected refusal: a single-request replay cannot interleave; "
                 "the demo's live race-refusal moment",
    },
    {
        "id": "F12",
        "title": "Cancel loses its pop-with-default and dels blindly",
        "pattern": "guard-removal-keyerror",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "defensive pop(key, None) rewritten as index plus "
                          "del; cancelling an unknown order crashes"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "    order = ORDERS.pop(order_id, None)\n"
                    "    if order is None:\n"
                    "        raise HTTPException(status_code=404, detail=f\"no order {order_id}\")\n",
            "replace": "    order = ORDERS[order_id]\n"
                       "    del ORDERS[order_id]\n"}],
        "trigger": {"method": "POST", "path": "/orders/999999/cancel"},
        "expected_exception": "KeyError",
        "expected_c3": "healed",
    },
    {
        "id": "F13",
        "title": "Restock count loses its integer coercion",
        "pattern": "boundary-type-confusion",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "same boundary family as F05, surfacing in a "
                          "comparison instead of arithmetic"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "    count = int(payload.get(\"count\", 0))\n",
            "replace": "    count = payload.get(\"count\", 0)\n"}],
        "trigger": {"method": "POST", "path": "/restock/WIDGET",
                    "json": {"count": "5"}},
        "expected_exception": "TypeError",
        "expected_c3": "healed",
    },
    {
        "id": "F14",
        "title": "Unknown-SKU guard removed from /reserve",
        "pattern": "guard-removal-keyerror",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "existence check dropped ahead of a keyed access "
                          "to shared state"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "def reserve(sku: str) -> dict:\n"
                    "    if sku not in STOCK:\n"
                    "        raise HTTPException(status_code=404, detail=f\"unknown sku {sku!r}\")\n"
                    "    with _stock_lock:\n",
            "replace": "def reserve(sku: str) -> dict:\n"
                       "    with _stock_lock:\n"}],
        "trigger": {"method": "POST", "path": "/reserve/UNOBTAINIUM"},
        "expected_exception": "KeyError",
        "expected_c3": "healed",
    },
    {
        "id": "F15",
        "title": "Per-unit maths gains an off-by-one denominator",
        "pattern": "off-by-one-divzero",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "an off-by-one introduced into a denominator; the "
                          "default argument value is exactly the zero case"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "            \"per_unit\": round(total / quantity, 2),\n",
            "replace": "            \"per_unit\": round(total / (quantity - 1), 2),\n"}],
        "trigger": {"method": "GET", "path": "/quote",
                    "params": {"sku": "WIDGET"}},
        "expected_exception": "ZeroDivisionError",
        "expected_c3": "healed",
    },
    {
        "id": "F16",
        "title": "Discount handler references a renamed variable",
        "pattern": "rename-nameerror",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "a variable renamed at its binding site but not at "
                          "one use; NameError on the untouched path"},
        "service": "alpha",
        "deterministic": True,
        "stateful": True,
        "mutations": [{
            "file": ALPHA,
            "find": "    rate = discount_rate(code)\n"
                    "    discounted_total = order[\"total\"] * (1 - rate)\n",
            "replace": "    rate = discount_rate(coupon)\n"
                       "    discounted_total = order[\"total\"] * (1 - rate)\n"}],
        "setup": [{"method": "POST", "path": "/orders",
                   "json": {"lines": [{"sku": "WIDGET", "quantity": 1}]}}],
        "trigger": {"method": "GET", "path": "/orders/1/discounted",
                    "params": {"code": "SAVE10"}},
        "expected_exception": "NameError",
        "expected_c3": "refused",
        "notes": "stateful: the 404 guard fires first in a fresh replay, so "
                 "the captured failure is unreachable",
    },
    {
        "id": "F17",
        "title": "Order line validation dropped, missing sku indexes",
        "pattern": "validation-removal-keyerror",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "request validation removed; the handler indexes a "
                          "field the malformed request never carried"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "    for line in lines:\n"
                    "        if \"sku\" not in line:\n"
                    "            raise HTTPException(status_code=422, detail=\"line missing sku\")\n"
                    "        try:\n",
            "replace": "    for line in lines:\n"
                       "        try:\n"}],
        "trigger": {"method": "POST", "path": "/orders",
                    "json": {"lines": [{"quantity": 2}]}},
        "expected_exception": "KeyError",
        "expected_c3": "healed",
    },
    {
        "id": "F18",
        "title": "Order total accumulator initialised as a string",
        "pattern": "wrong-initialiser-type",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "an accumulator's initialiser changed type; the "
                          "first += mixes str and float"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "    total = 0.0\n",
            "replace": "    total = \"\"\n"}],
        "trigger": {"method": "POST", "path": "/orders",
                    "json": {"lines": [{"sku": "DOODAD", "quantity": 1}]}},
        "expected_exception": "TypeError",
        "expected_c3": "healed",
    },
    {
        "id": "F19",
        "title": "Discount rate assigned only inside a new conditional",
        "pattern": "conditional-unboundlocal",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "an assignment moved under a condition during a "
                          "refactor; the fall-through path reads an unbound "
                          "local"},
        "service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": ALPHA,
            "find": "    rate = discount_rate(code)\n"
                    "    total = unit * quantity * (1 - rate)\n",
            "replace": "    if code:\n"
                       "        rate = discount_rate(code)\n"
                       "    total = unit * quantity * (1 - rate)\n"}],
        "trigger": {"method": "GET", "path": "/quote",
                    "params": {"sku": "GADGET"}},
        "expected_exception": "UnboundLocalError",
        "expected_c3": "healed",
    },
    {
        "id": "F20",
        "title": "Beta's unknown-SKU guard removed",
        "pattern": "guard-removal-keyerror",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "the dependency's own input guard lost; the fault "
                          "and the incident are both in beta"},
        "service": "beta",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": BETA,
            "find": "def price(sku: str) -> dict:\n"
                    "    if sku not in PRICES:\n"
                    "        raise HTTPException(status_code=404, detail=f\"unknown sku {sku!r}\")\n"
                    "    return {\"sku\": sku, \"price\": PRICES[sku]}\n",
            "replace": "def price(sku: str) -> dict:\n"
                       "    return {\"sku\": sku, \"price\": PRICES[sku]}\n"}],
        "trigger": {"method": "GET", "path": "/price/UNOBTAINIUM"},
        "expected_exception": "KeyError",
        "expected_c3": "healed",
    },
    {
        "id": "F21",
        "title": "Beta starts returning the price as a string",
        "pattern": "cross-service-type-drift",
        "provenance": {
            "origin": "seeded",
            "modeled_on": "dependency serialisation change; the fault lives "
                          "in beta but the crash and the capture form in "
                          "alpha, so the platform can only patch the consumer"},
        "service": "beta",
        "trigger_service": "alpha",
        "deterministic": True,
        "stateful": False,
        "mutations": [{
            "file": BETA,
            "find": "    return {\"sku\": sku, \"price\": PRICES[sku]}\n",
            "replace": "    return {\"sku\": sku, \"price\": str(PRICES[sku])}\n"}],
        "trigger": {"method": "GET", "path": "/quote",
                    "params": {"sku": "WIDGET", "quantity": 2}},
        "expected_exception": "TypeError",
        "expected_c3": "healed",
        "notes": "an admitted alpha-side patch is defensive coercion; the "
                 "corpus true fix is beta-side. Discussion material on where "
                 "a repair belongs",
    },
]


def main() -> int:
    problems = []
    sources = {}
    for entry in ENTRIES:
        for m in entry["mutations"]:
            path = os.path.join(SERVICES, m["file"])
            if path not in sources:
                sources[path] = open(path).read()
            source = sources[path]
            if source.count(m["find"]) != 1:
                problems.append(f"{entry['id']}: find occurs "
                                f"{source.count(m['find'])} times in {m['file']}")
                continue
            mutated = source.replace(m["find"], m["replace"])
            if mutated.count(m["replace"]) != 1:
                problems.append(f"{entry['id']}: replace not unique after "
                                f"injection in {m['file']}")
    if problems:
        print("REFUSING to write, corpus out of sync with clean services:")
        for p in problems:
            print(" ", p)
        return 1

    os.makedirs(FAULTS, exist_ok=True)
    for stale in os.listdir(FAULTS):
        if stale.endswith(".json"):
            os.remove(os.path.join(FAULTS, stale))
    for entry in ENTRIES:
        with open(os.path.join(FAULTS, f"{entry['id']}.json"), "w") as fh:
            json.dump(entry, fh, indent=2)
            fh.write("\n")
    print(f"wrote {len(ENTRIES)} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
