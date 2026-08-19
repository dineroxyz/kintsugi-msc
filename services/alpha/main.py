"""Managed demo service `alpha`.

A small order service, deliberately ordinary: FastAPI handlers, one outbound
dependency (the `beta` pricing service), and the Kintsugi capture agent as
its only unusual component. This tree is the CLEAN release; every study
fault is injected into a copy of it by the corpus injector, never committed
here.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException

from libs.kintsugi_capture import CaptureClient, CaptureMiddleware

BETA_URL = os.environ.get("BETA_URL", "http://localhost:8102")

app = FastAPI(title="alpha")
app.add_middleware(CaptureMiddleware, service="alpha")

_pricing = CaptureClient(timeout=5.0)

# naive in-memory store; realistic enough for a demo service
ORDERS: dict[int, dict] = {}
NEXT_ID = iter(range(1, 10**6))

DISCOUNTS = {"SAVE10": 0.10, "SAVE20": 0.20}


def discount_rate(code: str) -> float:
    """Named codes from the table; PCT<n> codes give n percent off."""
    if code.startswith("PCT") and code[3:].isdigit():
        return min(int(code[3:]), 90) / 100.0
    return DISCOUNTS.get(code, 0.0)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "alpha"}


@app.post("/orders")
def create_order(order: dict) -> dict:
    """Create an order, pricing each line through the beta service."""
    lines = order.get("lines", [])
    if not lines:
        raise HTTPException(status_code=422, detail="order has no lines")
    total = 0.0
    for line in lines:
        resp = _pricing.get(f"{BETA_URL}/price/{line['sku']}")
        resp.raise_for_status()
        price = resp.json()["price"]
        quantity = int(line.get("quantity", 1))
        total += price * quantity
    oid = next(NEXT_ID)
    ORDERS[oid] = {"id": oid, "first_sku": lines[0]["sku"],
                   "lines": lines, "total": round(total, 2)}
    return ORDERS[oid]


@app.get("/orders/{order_id}")
def get_order(order_id: int) -> dict:
    if order_id not in ORDERS:
        raise HTTPException(status_code=404, detail=f"no order {order_id}")
    return ORDERS[order_id]


@app.get("/quote")
def quote(sku: str, quantity: int = 1, code: str = "") -> dict:
    """Quote a price for a SKU with an optional discount code."""
    if quantity < 1:
        raise HTTPException(status_code=422, detail="quantity must be at least 1")
    resp = _pricing.get(f"{BETA_URL}/price/{sku}")
    resp.raise_for_status()
    unit = resp.json()["price"]
    rate = discount_rate(code)
    total = unit * quantity * (1 - rate)
    return {"sku": sku, "quantity": quantity,
            "per_unit": round(total / quantity, 2),
            "total": round(total, 2)}


@app.get("/orders/{order_id}/discounted")
def discounted(order_id: int, code: str = "") -> dict:
    """Apply a discount code to an existing order."""
    if order_id not in ORDERS:
        raise HTTPException(status_code=404, detail=f"no order {order_id}")
    order = ORDERS[order_id]
    rate = discount_rate(code)
    discounted_total = order["total"] * (1 - rate)
    return {"id": order_id, "total": order["total"],
            "discounted": round(discounted_total, 2)}
