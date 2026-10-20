"""The gate's behaviour condition: a repair may change only the failure.

The patched service and the last good release both receive the same fixed
battery of requests (every route, with valid, boundary and invalid inputs,
plus the failure's own set-up and trigger). A patch is rejected if any
answer differs in substance from the last good release: a success that
differs, or one side failing where the other does not. Differences confined
to the wording of an error, or to which 4xx code is used, are recorded but
allowed, since a correct repair may phrase a refusal differently.
"""

from __future__ import annotations

import os

import httpx

from control_plane.deploy.live import ServiceProcess, free_port

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKUS = ["WIDGET", "GADGET", "SPROCKET", "DOODAD", "UNOBTAINIUM"]


def battery() -> list[dict]:
    """The fixed request sequence; 'svc' says which service receives it."""
    b: list[dict] = [{"svc": "beta", "method": "GET", "path": "/health"}]
    for s in SKUS:
        b += [{"svc": "beta", "method": "GET", "path": f"/price/{s}"},
              {"svc": "beta", "method": "GET", "path": f"/catalog/{s}"}]
        for cur in ("USD", "EUR", "GBP"):
            b.append({"svc": "beta", "method": "GET", "path": f"/price/{s}/converted",
                      "params": {"currency": cur}})
    for name in ("STARTER", "PRO", "EMPTY", "NOPE"):
        b.append({"svc": "beta", "method": "GET", "path": f"/bundles/{name}"})
    for tier in ("bronze", "silver", "gold", "platinum"):
        b.append({"svc": "beta", "method": "GET", "path": f"/discounts/{tier}"})

    a = lambda method, path, **kw: {"svc": "alpha", "method": method, "path": path, **kw}  # noqa: E731
    b.append(a("GET", "/health"))
    for s in SKUS:
        for q in (1, 2, 0):
            b.append(a("GET", "/quote", params={"sku": s, "quantity": q}))
    for code in ("", "SAVE10", "SAVE20", "PCT15", "PCT95", "PCTXX", "BOGUS"):
        b.append(a("GET", "/quote", params={"sku": "WIDGET", "quantity": 3, "code": code}))
    for body in ({"lines": [{"sku": "WIDGET", "quantity": 2}]},
                 {"lines": [{"sku": "GADGET", "quantity": 1}, {"sku": "DOODAD"}]},
                 {"lines": [{"sku": "SPROCKET", "quantity": "2"}]},
                 {"lines": [{"quantity": 2}]}, {"lines": []}, {},
                 {"lines": [{"sku": "UNOBTAINIUM", "quantity": 1}]}):
        b.append(a("POST", "/orders", json=body))
    for oid in (1, 2, 999999):
        b += [a("GET", f"/orders/{oid}"), a("GET", f"/orders/{oid}/receipt")]
        for code in ("", "SAVE10", "PCT15", "BOGUS"):
            b.append(a("GET", f"/orders/{oid}/discounted", params={"code": code}))
    for mt in ("0", "50", "abc"):
        b.append(a("GET", "/summary", params={"min_total": mt}))
    for count in (5, "3", 0):
        b.append(a("POST", "/restock/SPROCKET", json={"count": count}))
    for s in ("WIDGET", "SPROCKET", "UNOBTAINIUM"):
        b.append(a("POST", f"/reserve/{s}"))
    b += [a("POST", "/orders/2/cancel"), a("POST", "/orders/999999/cancel"),
          a("GET", "/orders/2")]
    for item in ({"sku": "WIDGET", "quantity": 1}, {"sku": "GADGET", "quantity": "2"},
                 {"quantity": 2}, {"sku": "UNOBTAINIUM"}):
        b.append(a("POST", "/carts/k1/items", json=item))
    b.append(a("POST", "/carts/k2/items", json={"sku": "DOODAD", "quantity": 3}))
    for cid in ("k1", "k2", "ghost"):
        b.append(a("GET", f"/carts/{cid}"))
        for r in (10, 0, 150):
            b.append(a("GET", f"/carts/{cid}/tax", params={"rate_pct": r}))
    b += [a("POST", "/carts/k2/checkout"), a("POST", "/carts/k2/checkout"),
          a("POST", "/carts/ghost/checkout"), a("POST", "/carts/k1/checkout"),
          a("GET", "/summary")]
    return b


def run_battery(tree: str, requests: list[dict]) -> list[tuple]:
    beta = ServiceProcess(tree, "beta.main:app", free_port(), "ref", ROOT)
    beta.start()
    alpha = ServiceProcess(tree, "alpha.main:app", free_port(), "ref", ROOT,
                           extra_env={"BETA_URL": f"http://127.0.0.1:{beta.port}"})
    alpha.start()
    out = []
    try:
        with httpx.Client(timeout=10) as c:
            for r in requests:
                port = alpha.port if r["svc"] == "alpha" else beta.port
                try:
                    resp = c.request(r["method"], f"http://127.0.0.1:{port}{r['path']}",
                                     params=r.get("params"), json=r.get("json"))
                    try:
                        body = resp.json()
                    except ValueError:
                        body = resp.text[:200]
                    out.append((resp.status_code, body))
                except httpx.HTTPError as exc:
                    out.append((0, type(exc).__name__))
    finally:
        alpha.stop()
        beta.stop()
    return out


def compare(ref: tuple, cand: tuple) -> str:
    if ref == cand:
        return "same"
    (rs, _), (cs, _) = ref, cand
    if 400 <= rs < 500 and 400 <= cs < 500:
        return "wording" if rs == cs else "error_code"
    return "behaviour"


RANK = {"same": 0, "wording": 1, "error_code": 2, "behaviour": 3}


def behaviour_check(reference_tree: str, patched_tree: str, extra: list[dict] | None = None) -> dict:
    """Compare the patched tree with the last good release."""
    reqs = battery() + list(extra or [])
    ref, cand = run_battery(reference_tree, reqs), run_battery(patched_tree, reqs)
    worst, diffs, excluded = "same", [], 0
    for r, x, y in zip(reqs, ref, cand):
        if x[0] >= 500 or x[0] == 0:
            excluded += 1
            continue
        kind = compare(x, y)
        if kind != "same":
            diffs.append({"request": f"{r['method']} {r['path']}", "kind": kind,
                          "reference": x[0], "patched": y[0]})
        worst = max(worst, kind, key=RANK.get)
    return {"passed": worst != "behaviour", "worst": worst, "requests": len(reqs) - excluded,
            "differences": diffs[:12]}
