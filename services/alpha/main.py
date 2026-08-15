"""Managed demo service `alpha`.

A small order service, deliberately ordinary: FastAPI handlers, one outbound
dependency (the `beta` pricing service), and the Kintsugi capture agent as
its only unusual component. Faults are injected by the corpus tooling; the
service itself is written straight.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from libs.kintsugi_capture import CaptureClient, CaptureMiddleware

BETA_URL = os.environ.get("BETA_URL", "http://localhost:8102")

app = FastAPI(title="alpha")
app.add_middleware(CaptureMiddleware, service="alpha")

_pricing = CaptureClient(timeout=5.0)

# naive in-memory store; realistic enough for a demo service
ORDERS: dict[int, dict] = {}
NEXT_ID = iter(range(1, 10**6))


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "alpha"}


@app.post("/orders")
def create_order(order: dict) -> dict:
    """Create an order, pricing each line through the beta service."""
    total = 0.0
    for line in order.get("lines", []):
        resp = _pricing.get(f"{BETA_URL}/price/{line['sku']}")
        resp.raise_for_status()
        price = resp.json()["price"]
        total += price * line["quantity"]
    oid = next(NEXT_ID)
    ORDERS[oid] = {"id": oid, "lines": order.get("lines", []), "total": round(total, 2)}
    return ORDERS[oid]


@app.get("/orders/{order_id}")
def get_order(order_id: int) -> dict:
    return ORDERS[order_id]


@app.get("/orders/{order_id}/discounted")
def discounted(order_id: int, code: str = "") -> dict:
    """Apply a discount code. The shape of handler the corpus faults target."""
    order = ORDERS[order_id]
    rate = {"SAVE10": 0.10, "SAVE20": 0.20}.get(code)
    discounted_total = order["total"] * (1 - rate)  # rate is None for unknown codes
    return {"id": order_id, "total": order["total"], "discounted": round(discounted_total, 2)}
