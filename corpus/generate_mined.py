"""Regenerate the MINED corpus entries (M*) against the current clean services.

Each entry transplants the failure mechanism of one mined OSS fix commit
(corpus/mining/candidates.jsonl) onto the managed services, with provenance
citing the upstream commit. The transplant preserves the failure class and
the mechanism shape (what kind of guard was lost, what kind of value flowed
where), not the upstream API. Where the mapping is loose the entry's notes
say so.

    python corpus/generate_mined.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from corpus.generate_seeded import verify_and_write

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANDIDATES = [json.loads(line) for line in
              open(os.path.join(ROOT, "corpus", "mining", "candidates.jsonl"))]

ALPHA = "alpha/main.py"
BETA = "beta/main.py"


def mined(index: int, modeled_on: str) -> dict:
    candidate = CANDIDATES[index]
    return {"origin": "mined",
            "source": candidate["url"],
            "upstream_repo": candidate["repo"],
            "upstream_commit": candidate["message"],
            "failure_class_upstream": candidate["failure_class"],
            "modeled_on": modeled_on}


SUMMARY_GUARD = ("    try:\n"
                 "        floor = float(min_total)\n"
                 "    except ValueError:\n"
                 "        raise HTTPException(status_code=422, "
                 "detail=\"min_total must be numeric\")\n")
PICKED = ("    picked = [o for o in ORDERS.values() "
          "if o[\"total\"] >= floor]\n")
NOTIFY_BLOCK = ("    ORDER_EVENTS.append({\"order\": oid, \"total\": "
                "ORDERS[oid][\"total\"]})\n"
                "    if _notifier is not None:\n"
                "        _notifier.send(ORDER_EVENTS[-1])\n")
CONVERTED_GUARD = ("    rate = FX_RATES.get(currency)\n"
                   "    if rate is None:\n"
                   "        raise HTTPException(status_code=422,\n"
                   "                            detail=f\"unsupported "
                   "currency {currency!r}\")\n")
RECEIPT_HEAD = ("    if order_id not in ORDERS:\n"
                "        raise HTTPException(status_code=404, "
                "detail=f\"no order {order_id}\")\n"
                "    order = ORDERS[order_id]\n"
                "    parts = [f\"{line['sku']} "
                "x{int(line.get('quantity', 1))}\"\n")

VALID_ORDER = {"lines": [{"sku": "WIDGET", "quantity": 2}]}

ENTRIES = [
    {
        "id": "M01",
        "title": "Summary loses the guard around its numeric conversion",
        "pattern": "unguarded-conversion",
        "provenance": mined(0, "a type conversion of untrusted input had its "
                               "failure handling removed"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA, "find": SUMMARY_GUARD,
                       "replace": "    floor = float(min_total)\n"}],
        "trigger": {"method": "GET", "path": "/summary",
                    "params": {"min_total": "abc"}},
        "expected_exception": "ValueError", "expected_c3": "healed",
    },
    {
        "id": "M02",
        "title": "Summary compares totals against the raw query string",
        "pattern": "boundary-type-confusion",
        "provenance": mined(2, "unvalidated query parameter type flows into "
                               "a typed operation"),
        "service": "alpha", "deterministic": True, "stateful": True,
        "mutations": [{"file": ALPHA, "find": PICKED,
                       "replace": "    picked = [o for o in ORDERS.values() "
                                  "if o[\"total\"] >= min_total]\n"}],
        "setup": [{"method": "POST", "path": "/orders", "json": VALID_ORDER}],
        "trigger": {"method": "GET", "path": "/summary"},
        "expected_exception": "TypeError", "expected_c3": "refused",
        "notes": "stateful: the comparison only runs once an order exists",
    },
    {
        "id": "M03",
        "title": "Order notifier called without its None guard",
        "pattern": "optional-component-none",
        "provenance": mined(18, "publish path stopped checking whether the "
                                "producer exists before using it"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA, "find": NOTIFY_BLOCK,
                       "replace": "    ORDER_EVENTS.append({\"order\": oid, "
                                  "\"total\": ORDERS[oid][\"total\"]})\n"
                                  "    _notifier.send(ORDER_EVENTS[-1])\n"}],
        "trigger": {"method": "POST", "path": "/orders", "json": VALID_ORDER},
        "expected_exception": "AttributeError", "expected_c3": "healed",
    },
    {
        "id": "M04",
        "title": "Order reference concatenates the raw id",
        "pattern": "key-building-type-confusion",
        "provenance": mined(11, "identifier of an unexpected type reaches "
                                "string key construction"),
        "service": "alpha", "deterministic": True, "stateful": True,
        "mutations": [{"file": ALPHA,
                       "find": "def order_reference(order_id: int) -> str:\n"
                               "    return \"ORD-\" + format(order_id, \"06d\")\n",
                       "replace": "def order_reference(order_id: int) -> str:\n"
                                  "    return \"ORD-\" + order_id\n"}],
        "setup": [{"method": "POST", "path": "/orders", "json": VALID_ORDER}],
        "trigger": {"method": "GET", "path": "/orders/1/receipt"},
        "expected_exception": "TypeError", "expected_c3": "refused",
        "notes": "stateful: the receipt guard 404s in a fresh replay",
    },
    {
        "id": "M05",
        "title": "Currency conversion loses its unsupported-currency check",
        "pattern": "validation-removal-none-flow",
        "provenance": mined(1, "reverting boundary validation lets an "
                               "unhandled value flow into arithmetic"),
        "service": "beta", "deterministic": True, "stateful": False,
        "mutations": [{"file": BETA, "find": CONVERTED_GUARD,
                       "replace": "    rate = FX_RATES.get(currency)\n"}],
        "trigger": {"method": "GET", "path": "/price/WIDGET/converted",
                    "params": {"currency": "GBP"}},
        "expected_exception": "TypeError", "expected_c3": "healed",
    },
    {
        "id": "M06",
        "title": "Summary reads order totals as attributes",
        "pattern": "dict-attribute-confusion",
        "provenance": mined(4, "value of one kind handled with the access "
                               "pattern of another after a coercion change"),
        "service": "alpha", "deterministic": True, "stateful": True,
        "mutations": [{"file": ALPHA, "find": PICKED,
                       "replace": "    picked = [o for o in ORDERS.values() "
                                  "if o.total >= floor]\n"}],
        "setup": [{"method": "POST", "path": "/orders", "json": VALID_ORDER}],
        "trigger": {"method": "GET", "path": "/summary"},
        "expected_exception": "AttributeError", "expected_c3": "refused",
    },
    {
        "id": "M07",
        "title": "Catalog tag lookup loses its default, len sees None",
        "pattern": "misidentified-absent-value",
        "provenance": mined(5, "a lookup that can miss is treated as if it "
                               "always yields the expected type"),
        "service": "beta", "deterministic": True, "stateful": False,
        "mutations": [{"file": BETA,
                       "find": "    tags = CATALOG_TAGS.get(sku, [])\n",
                       "replace": "    tags = CATALOG_TAGS.get(sku)\n"}],
        "trigger": {"method": "GET", "path": "/catalog/DOODAD"},
        "expected_exception": "TypeError", "expected_c3": "healed",
        "notes": "DOODAD is priced but has no catalog entry, so the lookup "
                 "yields None and len(None) raises",
    },
    {
        "id": "M08",
        "title": "Quote calls a renamed response method",
        "pattern": "api-attribute-drift",
        "provenance": mined(6, "call site not updated when the object's "
                               "inspection API changed"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "    resp.raise_for_status()\n"
                               "    unit = resp.json()[\"price\"]\n",
                       "replace": "    resp.raise_status()\n"
                                  "    unit = resp.json()[\"price\"]\n"}],
        "trigger": {"method": "GET", "path": "/quote",
                    "params": {"sku": "WIDGET"}},
        "expected_exception": "AttributeError", "expected_c3": "healed",
    },
    {
        "id": "M09",
        "title": "Currency conversion indexes the rate table blindly",
        "pattern": "guard-removal-keyerror",
        "provenance": mined(7, "a keyed lookup ran outside the narrow case "
                               "its guard was written for"),
        "service": "beta", "deterministic": True, "stateful": False,
        "mutations": [{"file": BETA, "find": CONVERTED_GUARD,
                       "replace": "    rate = FX_RATES[currency]\n"}],
        "trigger": {"method": "GET", "path": "/price/GADGET/converted",
                    "params": {"currency": "GBP"}},
        "expected_exception": "KeyError", "expected_c3": "healed",
    },
    {
        "id": "M10",
        "title": "Summary guard catches the wrong exception class",
        "pattern": "wrong-exception-caught",
        "provenance": mined(8, "except clause names a different exception "
                               "class than the operation actually raises"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "    except ValueError:\n",
                       "replace": "    except TypeError:\n"}],
        "trigger": {"method": "GET", "path": "/summary",
                    "params": {"min_total": "abc"}},
        "expected_exception": "ValueError", "expected_c3": "healed",
    },
    {
        "id": "M11",
        "title": "Catalog indexes the first tag of a tagless SKU",
        "pattern": "truncated-input-indexerror",
        "provenance": mined(10, "decoder indexes into input shorter than "
                                "the code assumes"),
        "service": "beta", "deterministic": True, "stateful": False,
        "mutations": [{"file": BETA,
                       "find": "            \"primary_tag\": tags[0] if tags else None,\n",
                       "replace": "            \"primary_tag\": tags[0],\n"}],
        "trigger": {"method": "GET", "path": "/catalog/SPROCKET"},
        "expected_exception": "IndexError", "expected_c3": "healed",
    },
    {
        "id": "M12",
        "title": "Restock raises a bare ValueError instead of its HTTP error",
        "pattern": "wrong-error-surface",
        "provenance": mined(12, "failure path raises a bare builtin instead "
                                "of the intended clear error"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "        raise HTTPException(status_code=422, "
                               "detail=\"count must be at least 1\")\n",
                       "replace": "        raise ValueError(\"count must be "
                                  "at least 1\")\n"}],
        "trigger": {"method": "POST", "path": "/restock/WIDGET",
                    "json": {"count": 0}},
        "expected_exception": "ValueError", "expected_c3": "healed",
    },
    {
        "id": "M13",
        "title": "Event append consumes the order it reports",
        "pattern": "lifecycle-state-consumed",
        "provenance": mined(13, "bookkeeping on one path mutates state "
                                "another path still needs"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "    ORDER_EVENTS.append({\"order\": oid, "
                               "\"total\": ORDERS[oid][\"total\"]})\n",
                       "replace": "    ORDER_EVENTS.append({\"order\": oid, "
                                  "\"total\": ORDERS.pop(oid)[\"total\"]})\n"}],
        "trigger": {"method": "POST", "path": "/orders", "json": VALID_ORDER},
        "expected_exception": "KeyError", "expected_c3": "healed",
    },
    {
        "id": "M14",
        "title": "Order total rounded with a string precision",
        "pattern": "wrong-argument-type",
        "provenance": mined(17, "call assembled with an argument of the "
                                "wrong type on one code path (loose mapping)"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "                   \"lines\": lines, "
                               "\"total\": round(total, 2)}\n",
                       "replace": "                   \"lines\": lines, "
                                  "\"total\": round(total, \"2\")}\n"}],
        "trigger": {"method": "POST", "path": "/orders", "json": VALID_ORDER},
        "expected_exception": "TypeError", "expected_c3": "healed",
    },
    {
        "id": "M15",
        "title": "Receipt fetches its order without the existence guard",
        "pattern": "optional-component-none",
        "provenance": mined(15, "control path stopped guarding an object "
                                "that can legitimately be absent"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA, "find": RECEIPT_HEAD,
                       "replace": "    order = ORDERS.get(order_id)\n"
                                  "    parts = [f\"{line['sku']} "
                                  "x{int(line.get('quantity', 1))}\"\n"}],
        "trigger": {"method": "GET", "path": "/orders/999999/receipt"},
        "expected_exception": "TypeError", "expected_c3": "healed",
    },
    {
        "id": "M16",
        "title": "Quote reference formats the SKU with an integer code",
        "pattern": "introspects-wrong-object",
        "provenance": mined(16, "code follows the wrong attribute of an "
                                "object and operates on the wrong value"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "            \"reference\": \"Q\" + "
                               "format(quantity, \"03d\")}\n",
                       "replace": "            \"reference\": \"Q\" + "
                                  "format(sku, \"03d\")}\n"}],
        "trigger": {"method": "GET", "path": "/quote",
                    "params": {"sku": "WIDGET"}},
        "expected_exception": "ValueError", "expected_c3": "healed",
    },
    {
        "id": "M17",
        "title": "Reserved unit decoded as if it were bytes",
        "pattern": "bytes-str-compat-drift",
        "provenance": mined(14, "client library version drift changes the "
                                "value type a call site assumes"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "    return {\"sku\": sku, \"unit\": unit}\n",
                       "replace": "    return {\"sku\": sku, \"unit\": "
                                  "unit.decode()}\n"}],
        "trigger": {"method": "POST", "path": "/reserve/WIDGET"},
        "expected_exception": "AttributeError", "expected_c3": "healed",
    },
    {
        "id": "M18",
        "title": "Beta raises a bare KeyError for unknown SKUs",
        "pattern": "wrong-error-surface",
        "provenance": mined(3, "error path raises an inconsistent exception "
                               "class instead of the documented one"),
        "service": "beta", "deterministic": True, "stateful": False,
        "mutations": [{"file": BETA,
                       "find": "def price(sku: str) -> dict:\n"
                               "    if sku not in PRICES:\n"
                               "        raise HTTPException(status_code=404, "
                               "detail=f\"unknown sku {sku!r}\")\n",
                       "replace": "def price(sku: str) -> dict:\n"
                                  "    if sku not in PRICES:\n"
                                  "        raise KeyError(sku)\n"}],
        "trigger": {"method": "GET", "path": "/price/UNOBTAINIUM"},
        "expected_exception": "KeyError", "expected_c3": "healed",
    },
    {
        "id": "M19",
        "title": "Order id source left uninitialised",
        "pattern": "incomplete-initialisation",
        "provenance": mined(9, "component used before its initialisation "
                               "sequence completed"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "NEXT_ID = iter(range(1, 10**6))\n",
                       "replace": "NEXT_ID = None\n"}],
        "trigger": {"method": "POST", "path": "/orders", "json": VALID_ORDER},
        "expected_exception": "TypeError", "expected_c3": "healed",
    },
]


if __name__ == "__main__":
    sys.exit(verify_and_write(ENTRIES, "M"))
