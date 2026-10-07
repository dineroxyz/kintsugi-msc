"""Behavioural correctness of admitted patches by differential testing.

For every repair the gated configuration put live, two copies of the shop
are started: the reference (the clean release, i.e. the ground-truth fix)
and the candidate (the faulty release with the certificate's patches
applied, in the order they were issued). Both receive the same fixed
battery of requests in the same order: every route of both services with
valid, boundary and invalid inputs, followed by the fault's own setup and
trigger. Every response pair is compared.

Per request:
  same           identical status and body
  wording        both 4xx with the same status, different error text
  error_code     both 4xx, different status
  behaviour      anything else (a success differs, or one side fails)
Requests where the reference itself answers 5xx are excluded and listed.

A patch's verdict is its worst request. Patches with any `behaviour`
difference are listed with the differing requests for inspection.

    python study/differential.py            -> study/correctness_differential.json
"""

from __future__ import annotations

import collections
import glob
import json
import os
import shutil
import sys
import tempfile

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite://")

from control_plane.gate.verifier import _apply_patch  # noqa: E402
from control_plane.deploy.live import ServiceProcess, free_port  # noqa: E402
from corpus import load_entry  # noqa: E402
from corpus.injector import inject  # noqa: E402

SERVICES = os.path.join(ROOT, "services")
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


def fault_requests(entry) -> list[dict]:
    out = []
    for r in list(entry.setup) + [entry.trigger]:
        svc = entry.trigger_service if r is entry.trigger else "alpha"
        out.append({"svc": svc, **{k: v for k, v in r.items() if k != "url"}})
    return out


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


def admitted(study_dir: str, rows_filter=None) -> list[dict]:
    """Trials of the base gated arm that recovered, with their certificates."""
    certs = {}
    for p in glob.glob(os.path.join(study_dir, "certificates", "*.json")):
        c = json.load(open(p))
        certs[c["hash"]] = c
    out = []
    for line in open(os.path.join(study_dir, "trials.jsonl")):
        t = json.loads(line)
        if t["arm"] != "C3" or t.get("variant") or t["outcome"] != "recovered":
            continue
        hashes = (t.get("detail") or {}).get("certificates") or []
        chain = sorted((certs[h] for h in hashes if h in certs), key=lambda c: c["sequence"])
        if chain:
            out.append({"entry": t["entry"], "seed": t.get("seed"),
                        "patches": [c["patch"] for c in chain],
                        "sequences": [c["sequence"] for c in chain]})
    return out


def main() -> int:
    studies = {"main": os.path.join(ROOT, "study", "main"),
               "weaker_model": os.path.join(ROOT, "study", "haiku")}
    base = battery()
    work = tempfile.mkdtemp(prefix="kintsugi-diff-")
    reference_cache: dict[str, list] = {}
    results = []
    try:
        for study, d in studies.items():
            if not os.path.exists(os.path.join(d, "trials.jsonl")):
                continue
            # identical patch sets need testing only once
            groups = collections.defaultdict(list)
            for t in admitted(d):
                groups[(t["entry"], tuple(t["patches"]))].append(t)
            for (entry_id, patches), trials in sorted(groups.items()):
                entry = load_entry(entry_id)
                reqs = base + fault_requests(entry)
                if entry_id not in reference_cache:
                    reference_cache[entry_id] = run_battery(SERVICES, reqs)
                ref = reference_cache[entry_id]
                tree = inject(entry, SERVICES, os.path.join(work, f"{study}-{entry_id}-{len(results)}"))
                error = None
                for p in patches:
                    nxt, error = _apply_patch(tree, p)
                    if nxt is None:
                        break
                    tree = nxt
                if error and nxt is None:
                    results.append({"study": study, "entry": entry_id, "trials": len(trials),
                                    "verdict": "patch_did_not_apply", "error": error})
                    continue
                cand = run_battery(tree, reqs)
                diffs, excluded = [], 0
                worst = "same"
                for r, x, y in zip(reqs, ref, cand):
                    if x[0] >= 500 or x[0] == 0:
                        excluded += 1
                        continue
                    kind = compare(x, y)
                    if kind != "same":
                        diffs.append({"request": f"{r['method']} {r['path']} "
                                                 f"{json.dumps(r.get('params') or r.get('json') or '')}",
                                      "kind": kind, "reference": x, "patched": y})
                    worst = max(worst, kind, key=RANK.get)
                results.append({"study": study, "entry": entry_id, "trials": len(trials),
                                "seeds": [t["seed"] for t in trials],
                                "sequences": [t["sequences"] for t in trials],
                                "verdict": worst, "requests": len(reqs),
                                "excluded_reference_errors": excluded, "differences": diffs})
                print(f"{study:13s} {entry_id}  x{len(trials)}  {worst:11s} "
                      f"{len(diffs)} differing of {len(reqs) - excluded}", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    summary = {}
    for study in studies:
        rs = [r for r in results if r["study"] == study]
        summary[study] = {
            "distinct_patch_sets": len(rs), "trials": sum(r["trials"] for r in rs),
            "by_verdict_trials": dict(collections.Counter(
                v for r in rs for v in [r["verdict"]] * r["trials"])),
            "by_verdict_patch_sets": dict(collections.Counter(r["verdict"] for r in rs))}
    out = {"battery_size": len(base), "summary": summary, "results": results}
    with open(os.path.join(ROOT, "study", "correctness_differential.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
