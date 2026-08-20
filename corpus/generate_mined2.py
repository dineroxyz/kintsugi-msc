"""Regenerate the wave-2 MINED corpus entries (N*) against the clean services.

Same contract as generate_mined.py: each entry transplants the failure
mechanism of one mined OSS fix commit onto the managed services, provenance
citing the upstream commit. This wave targets the cart/bundle/tax surface
and adds transplanted concurrency faults from the async libraries mined in
round two.

    python corpus/generate_mined2.py
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
    c = CANDIDATES[index]
    return {"origin": "mined", "source": c["url"],
            "upstream_repo": c["repo"], "upstream_commit": c["message"],
            "failure_class_upstream": c["failure_class"],
            "modeled_on": modeled_on}


CART_IMPORTS = "import os\nimport threading\n\nimport httpx\n"
CART_LOCK_ADD = ("    with _cart_lock:\n"
                 "        cart = CARTS.setdefault(cart_id, [])\n"
                 "        cart.append({\"sku\": item[\"sku\"],\n"
                 "                     \"quantity\": int(item.get(\"quantity\", 1))})\n"
                 "        size = len(cart)\n")
FIRST_SKU = ("    first = items[0][\"sku\"] if items else None\n")
CHECKOUT_EMPTY = ("    items = CARTS.pop(cart_id)\n"
                  "    if not items:\n"
                  "        raise HTTPException(status_code=422, "
                  "detail=\"cart is empty\")\n")
TAX_RANGE = ("    if rate_pct < 0 or rate_pct > 100:\n"
             "        raise HTTPException(status_code=422, "
             "detail=\"rate_pct out of range\")\n")
BUNDLE_EMPTY = ("    if not members:\n"
                "        raise HTTPException(status_code=422, "
                "detail=\"bundle is empty\")\n")
LEADER = ("    prices = sorted((PRICES[s] for s in members), reverse=True)\n"
          "    return {\"bundle\": name, \"size\": len(members),\n"
          "            \"leader_price\": prices[0], \"total\": "
          "round(sum(prices), 2)}\n")

ITEM = {"sku": "WIDGET", "quantity": 1}


def cart_setup(cart_id, item=ITEM):
    return [{"method": "POST", "path": f"/carts/{cart_id}/items", "json": item}]


ENTRIES = [
    {
        "id": "N01",
        "title": "Empty-cart tax divides by zero units",
        "pattern": "off-by-context-divzero",
        "provenance": mined(20, "an operation valid for the normal case "
                               "divides by a quantity that can be zero"),
        "service": "alpha", "deterministic": True, "stateful": True,
        "mutations": [{"file": ALPHA,
                       "find": "            \"per_unit_tax\": round(base and "
                               "(base * rate_pct / 100) / base, 4)}\n",
                       "replace": "            \"per_unit_tax\": round("
                                  "(base * rate_pct / 100) / base, 4)}\n"}],
        "setup": [{"method": "POST", "path": "/carts/c1/items",
                   "json": {"sku": "WIDGET", "quantity": 0}}],
        "trigger": {"method": "GET", "path": "/carts/c1/tax"},
        "expected_exception": "ZeroDivisionError", "expected_c3": "refused",
        "notes": "stateful: needs a cart of zero-quantity items",
    },
    {
        "id": "N02",
        "title": "Cart read loses its existence guard",
        "pattern": "guard-removal-keyerror",
        "provenance": mined(26, "a keyed read performed without first "
                               "checking the key is present"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "def get_cart(cart_id: str) -> dict:\n"
                               "    if cart_id not in CARTS:\n"
                               "        raise HTTPException(status_code=404, detail=f\"no cart {cart_id}\")\n"
                               "    items = CARTS[cart_id]\n",
                       "replace": "def get_cart(cart_id: str) -> dict:\n"
                                  "    items = CARTS[cart_id]\n"}],
        "trigger": {"method": "GET", "path": "/carts/ghost"},
        "expected_exception": "KeyError", "expected_c3": "healed",
    },
    {
        "id": "N03",
        "title": "Cart append assumes the cart already exists",
        "pattern": "missing-initialisation-keyerror",
        "provenance": mined(31, "state accessed on a code path that no "
                               "longer creates it first"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "        cart = CARTS.setdefault(cart_id, [])\n",
                       "replace": "        cart = CARTS[cart_id]\n"}],
        "trigger": {"method": "POST", "path": "/carts/fresh/items",
                    "json": ITEM},
        "expected_exception": "KeyError", "expected_c3": "healed",
    },
    {
        "id": "N04",
        "title": "Bundle leader read before the empty check",
        "pattern": "reordered-guard-indexerror",
        "provenance": mined(24, "an index into possibly-empty content moved "
                               "ahead of the guard that protected it"),
        "service": "beta", "deterministic": True, "stateful": False,
        "mutations": [{"file": BETA, "find": BUNDLE_EMPTY + LEADER,
                       "replace": LEADER}],
        "trigger": {"method": "GET", "path": "/bundles/EMPTY"},
        "expected_exception": "IndexError", "expected_c3": "healed",
    },
    {
        "id": "N05",
        "title": "Tier discount divides bps by a string",
        "pattern": "wrong-argument-type",
        "provenance": mined(23, "a value of the wrong type reaches an "
                               "arithmetic operation on one path"),
        "service": "beta", "deterministic": True, "stateful": False,
        "mutations": [{"file": BETA,
                       "find": "    return {\"tier\": tier, \"bps\": bps, "
                               "\"fraction\": round(bps / 10000, 4)}\n",
                       "replace": "    return {\"tier\": tier, \"bps\": bps, "
                                  "\"fraction\": round(bps / \"10000\", 4)}\n"}],
        "trigger": {"method": "GET", "path": "/discounts/silver"},
        "expected_exception": "TypeError", "expected_c3": "healed",
    },
    {
        "id": "N06",
        "title": "Checkout empty-cart guard removed, sum over nothing then index",
        "pattern": "validation-removal-keyerror",
        "provenance": mined(19, "handling of a malformed/empty value removed, "
                               "later code assumes it was well formed"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "    if cart_id not in CARTS:\n"
                               "        raise HTTPException(status_code=404, detail=f\"no cart {cart_id}\")\n"
                               "    items = CARTS.pop(cart_id)\n",
                       "replace": "    items = CARTS.pop(cart_id)\n"}],
        "trigger": {"method": "POST", "path": "/carts/ghost/checkout"},
        "expected_exception": "KeyError", "expected_c3": "healed",
        "notes": "checkout of an unknown cart pops a missing key",
    },
    {
        "id": "N07",
        "title": "Tax range check compares string rate to integers",
        "pattern": "boundary-type-confusion",
        "provenance": mined(30, "an unset/typed default reaches a comparison "
                               "expecting another type"),
        "service": "alpha", "deterministic": True, "stateful": True,
        "mutations": [{"file": ALPHA,
                       "find": "def cart_tax(cart_id: str, rate_pct: int = 10) -> dict:\n",
                       "replace": "def cart_tax(cart_id: str, rate_pct: str = \"10\") -> dict:\n"}],
        "setup": cart_setup("t1"),
        "trigger": {"method": "GET", "path": "/carts/t1/tax"},
        "expected_exception": "TypeError", "expected_c3": "refused",
        "notes": "stateful: the tax read needs an existing cart",
    },
    {
        "id": "N08",
        "title": "Cart item quantity coercion dropped",
        "pattern": "boundary-type-confusion",
        "provenance": mined(34, "a cached/received value of an unexpected "
                               "type is used without coercion"),
        "service": "alpha", "deterministic": True, "stateful": True,
        "mutations": [{"file": ALPHA,
                       "find": "        cart.append({\"sku\": item[\"sku\"],\n"
                               "                     \"quantity\": int(item.get(\"quantity\", 1))})\n",
                       "replace": "        cart.append({\"sku\": item[\"sku\"],\n"
                                  "                     \"quantity\": item.get(\"quantity\", 1)})\n"}],
        "setup": [{"method": "POST", "path": "/carts/c9/items",
                   "json": {"sku": "WIDGET", "quantity": "3"}}],
        "trigger": {"method": "POST", "path": "/carts/c9/checkout"},
        "expected_exception": "TypeError", "expected_c3": "refused",
        "notes": "stateful: the string quantity is stored on add and only "
                 "raises when summed at checkout, which a fresh replay of "
                 "the checkout cannot reach",
    },
    {
        "id": "N09",
        "title": "Bundle price lookup loses its membership guard",
        "pattern": "guard-removal-keyerror",
        "provenance": mined(21, "a lookup performed without first checking "
                               "the key is present"),
        "service": "beta", "deterministic": True, "stateful": False,
        "mutations": [{"file": BETA,
                       "find": "    table = {\"bronze\": 100, \"silver\": 250, \"gold\": 500}\n"
                               "    if tier not in table:\n"
                               "        raise HTTPException(status_code=404, detail=f\"unknown tier {tier!r}\")\n",
                       "replace": "    table = {\"bronze\": 100, \"silver\": 250, \"gold\": 500}\n"}],
        "trigger": {"method": "GET", "path": "/discounts/platinum"},
        "expected_exception": "KeyError", "expected_c3": "healed",
    },
    {
        "id": "N10",
        "title": "Checkout pop without default on a concurrent double-submit",
        "pattern": "toctou-race",
        "provenance": mined(40, "socket/resource close races a cancelled "
                               "operation on the same object"),
        "service": "alpha", "deterministic": False, "stateful": True,
        "requires_concurrency": True,
        "mutations": [{"file": ALPHA,
                       "find": "    if cart_id not in CARTS:\n"
                               "        raise HTTPException(status_code=404, detail=f\"no cart {cart_id}\")\n"
                               "    items = CARTS.pop(cart_id)\n",
                       "replace": "    if cart_id not in CARTS:\n"
                                  "        raise HTTPException(status_code=404, detail=f\"no cart {cart_id}\")\n"
                                  "    import time as _t; _t.sleep(0.003)\n"
                                  "    items = CARTS.pop(cart_id)\n"}],
        "setup": cart_setup("dbl"),
        "trigger": {"method": "POST", "path": "/carts/dbl/checkout"},
        "expected_exception": "KeyError", "expected_c3": "refused",
        "notes": "two concurrent checkouts both pass the membership check; "
                 "the second pop raises",
    },
    {
        "id": "N11",
        "title": "Cart tax reads a renamed lock attribute",
        "pattern": "api-attribute-drift",
        "provenance": mined(32, "attribute access on a connection object "
                               "whose API changed across versions"),
        "service": "alpha", "deterministic": True, "stateful": True,
        "mutations": [{"file": ALPHA,
                       "find": "    base = sum(item[\"quantity\"] for item in CARTS[cart_id])\n",
                       "replace": "    base = sum(item.quantity for item in CARTS[cart_id])\n"}],
        "setup": cart_setup("t2"),
        "trigger": {"method": "GET", "path": "/carts/t2/tax"},
        "expected_exception": "AttributeError", "expected_c3": "refused",
    },
    {
        "id": "N12",
        "title": "Bundle total sums prices of a missing member",
        "pattern": "contract-drift-keyerror",
        "provenance": mined(38, "code reads a field/member the data no "
                               "longer guarantees"),
        "service": "beta", "deterministic": True, "stateful": False,
        "mutations": [{"file": BETA,
                       "find": "    \"PRO\": [\"GADGET\", \"WIDGET\", \"DOODAD\"],\n",
                       "replace": "    \"PRO\": [\"GADGET\", \"WIDGET\", \"PHANTOM\"],\n"}],
        "trigger": {"method": "GET", "path": "/bundles/PRO"},
        "expected_exception": "KeyError", "expected_c3": "healed",
    },
    {
        "id": "N13",
        "title": "Cart checkout builds price URL from the whole item dict",
        "pattern": "key-building-type-confusion",
        "provenance": mined(43, "a value of the wrong type reaches string "
                               "/key construction outside its expected scope"),
        "service": "alpha", "deterministic": True, "stateful": True,
        "mutations": [{"file": ALPHA,
                       "find": "        total += resp.json()[\"price\"] * item[\"quantity\"]\n",
                       "replace": "        total += resp.json()[\"price\"] * item[\"qty\"]\n"}],
        "setup": cart_setup("u1"),
        "trigger": {"method": "POST", "path": "/carts/u1/checkout"},
        "expected_exception": "KeyError", "expected_c3": "refused",
        "notes": "stateful: checkout reads a wrong key on the cart items a "
                 "fresh replay cannot rebuild",
    },
    {
        "id": "N14",
        "title": "Tier fraction formatted with a string width",
        "pattern": "wrong-argument-type",
        "provenance": mined(44, "a boundary value of the wrong type reaches "
                               "a call that rejects it"),
        "service": "beta", "deterministic": True, "stateful": False,
        "mutations": [{"file": BETA,
                       "find": "    bps = table[tier]\n",
                       "replace": "    bps = table[tier]\n    _ = round(bps, \"2\")\n"}],
        "trigger": {"method": "GET", "path": "/discounts/bronze"},
        "expected_exception": "TypeError", "expected_c3": "healed",
    },
    {
        "id": "N15",
        "title": "Cart items counter reads a renamed local",
        "pattern": "rename-nameerror",
        "provenance": mined(33, "a symbol renamed at one site but still "
                               "referenced under its old name elsewhere"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "        size = len(cart)\n"
                               "    return {\"cart\": cart_id, \"items\": size}\n",
                       "replace": "        size = len(cart)\n"
                                  "    return {\"cart\": cart_id, \"items\": count}\n"}],
        "trigger": {"method": "POST", "path": "/carts/c15/items",
                    "json": ITEM},
        "expected_exception": "NameError", "expected_c3": "healed",
    },
    {
        "id": "N16",
        "title": "Checkout accumulator initialised as None",
        "pattern": "wrong-initialiser-type",
        "provenance": mined(22, "an accumulator/option left as None reaches "
                               "an operation expecting a value"),
        "service": "alpha", "deterministic": True, "stateful": True,
        "mutations": [{"file": ALPHA,
                       "find": "    items = CARTS.pop(cart_id)\n"
                               "    if not items:\n"
                               "        raise HTTPException(status_code=422, detail=\"cart is empty\")\n"
                               "    total = 0.0\n",
                       "replace": "    items = CARTS.pop(cart_id)\n"
                                  "    if not items:\n"
                                  "        raise HTTPException(status_code=422, detail=\"cart is empty\")\n"
                                  "    total = None\n"}],
        "setup": cart_setup("acc"),
        "trigger": {"method": "POST", "path": "/carts/acc/checkout"},
        "expected_exception": "TypeError", "expected_c3": "refused",
        "notes": "stateful: needs a populated cart to reach the accumulation",
    },
    {
        "id": "N17",
        "title": "Bundle size reads len of a member string, not the list",
        "pattern": "introspects-wrong-object",
        "provenance": mined(35, "code operates on a differently-shaped value "
                               "than intended after a representation change"),
        "service": "beta", "deterministic": True, "stateful": False,
        "mutations": [{"file": BETA,
                       "find": "    return {\"bundle\": name, \"size\": len(members),\n"
                               "            \"leader_price\": prices[0], \"total\": round(sum(prices), 2)}\n",
                       "replace": "    return {\"bundle\": name, \"size\": len(members[0][99]),\n"
                                  "            \"leader_price\": prices[0], \"total\": round(sum(prices), 2)}\n"}],
        "trigger": {"method": "GET", "path": "/bundles/STARTER"},
        "expected_exception": "IndexError", "expected_c3": "healed",
    },
    {
        "id": "N18",
        "title": "Cart add guard checks the wrong membership",
        "pattern": "wrong-condition-keyerror",
        "provenance": mined(36, "a guard condition inverted or pointed at "
                               "the wrong object, leaving the real case open"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "    if \"sku\" not in item:\n"
                               "        raise HTTPException(status_code=422, detail=\"item missing sku\")\n"
                               "    with _cart_lock:\n",
                       "replace": "    with _cart_lock:\n"}],
        "trigger": {"method": "POST", "path": "/carts/c18/items",
                    "json": {"quantity": 2}},
        "expected_exception": "KeyError", "expected_c3": "healed",
        "notes": "the missing-sku guard removed; item['sku'] then raises",
    },
    {
        "id": "N19",
        "title": "Converted price parses scientific notation with int()",
        "pattern": "parse-without-guard",
        "provenance": mined(35, "a numeric string in an unexpected format "
                               "reaches a strict parser"),
        "service": "beta", "deterministic": True, "stateful": False,
        "mutations": [{"file": BETA,
                       "find": "    return {\"sku\": sku, \"currency\": currency,\n"
                               "            \"price\": round(PRICES[sku] * rate, 2)}\n",
                       "replace": "    return {\"sku\": sku, \"currency\": currency,\n"
                                  "            \"price\": int(str(PRICES[sku] * rate))}\n"}],
        "trigger": {"method": "GET", "path": "/price/DOODAD/converted",
                    "params": {"currency": "EUR"}},
        "expected_exception": "ValueError", "expected_c3": "healed",
        "notes": "DOODAD*0.92 is a fractional string; int() of it raises",
    },
    {
        "id": "N20",
        "title": "Heartbeat guard against a None dispatcher removed",
        "pattern": "optional-component-none",
        "provenance": mined(36, "a control command used a component without "
                               "guarding against its None state"),
        "service": "alpha", "deterministic": True, "stateful": False,
        "mutations": [{"file": ALPHA,
                       "find": "    if _notifier is not None:\n"
                               "        _notifier.send(ORDER_EVENTS[-1])\n",
                       "replace": "    _notifier.send(ORDER_EVENTS[-1]) "
                                  "if _notifier else _notifier.ping()\n"}],
        "trigger": {"method": "POST", "path": "/orders",
                    "json": {"lines": [{"sku": "GADGET", "quantity": 1}]}},
        "expected_exception": "AttributeError", "expected_c3": "healed",
    },
]


if __name__ == "__main__":
    sys.exit(verify_and_write(ENTRIES, "N"))
