"""Managed demo service `beta` — the pricing dependency of alpha.

Deliberately tiny: a static price table behind one endpoint, with the
capture agent attached like any other managed service. It exists so that
alpha's outbound calls are real HTTP in live runs, not stubs.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from libs.kintsugi_capture import CaptureMiddleware

PRICES = {
    "WIDGET": 25.0,
    "GADGET": 40.0,
    "SPROCKET": 9.5,
    "DOODAD": 3.25,
}

app = FastAPI(title="beta")
app.add_middleware(CaptureMiddleware, service="beta")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "beta"}


@app.get("/price/{sku}")
def price(sku: str) -> dict:
    if sku not in PRICES:
        raise HTTPException(status_code=404, detail=f"unknown sku {sku!r}")
    return {"sku": sku, "price": PRICES[sku]}
