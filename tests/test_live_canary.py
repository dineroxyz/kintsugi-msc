"""Live-process canary: the exit criterion for the deployment stage.

Real uvicorn subprocesses on real ports, a traffic-splitting proxy in front,
and the guard deciding on the same traffic the clients generated. Two paths:
a verified good patch is promoted and the live service is healed in place;
a bad canary breaches the guard and the live service is left untouched.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import httpx
import pytest
from fastapi.testclient import TestClient

from control_plane.deploy.live import (LiveCanaryController, ServiceProcess,
                                       TrafficSplitProxy, free_port)
from control_plane.loop import heal
from libs.kintsugi_capture import CaptureMiddleware

PLATFORM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVICE = textwrap.dedent('''
    from fastapi import FastAPI
    from libs.kintsugi_capture import CaptureMiddleware

    app = FastAPI()
    app.add_middleware(CaptureMiddleware, service="shop", release="s1")

    ORDERS = {1: {"id": 1, "total": 100.0}}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/orders/{oid}/discounted")
    def discounted(oid: int, code: str = ""):
        order = ORDERS[oid]
        rate = {"SAVE10": 0.10}.get(code)
        return {"id": oid, "discounted": round(order["total"] * (1 - rate), 2)}
''')

GOOD_DIFF = textwrap.dedent('''\
    --- a/shop_service/main.py
    +++ b/shop_service/main.py
    @@ -14,5 +14,5 @@
     @app.get("/orders/{oid}/discounted")
     def discounted(oid: int, code: str = ""):
         order = ORDERS[oid]
    -    rate = {"SAVE10": 0.10}.get(code)
    +    rate = {"SAVE10": 0.10}.get(code, 0.0)
         return {"id": oid, "discounted": round(order["total"] * (1 - rate), 2)}
''')


@pytest.fixture()
def live(tmp_path):
    """A faulty shop service running live, with proxy in front and one
    incident captured, healed offline to a verified patched tree."""
    root = tmp_path / "live"
    pkg = root / "shop_service"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text(SERVICE)

    # capture the incident in-process (same code, same capture agent)
    sys.path.insert(0, str(root))
    for name in [n for n in sys.modules if n.startswith("shop_service")]:
        del sys.modules[name]
    import importlib
    module = importlib.import_module("shop_service.main")
    events: list[dict] = []
    module.app.user_middleware.clear()
    module.app.middleware_stack = None
    module.app.add_middleware(CaptureMiddleware, service="shop", release="s1",
                              deliver=events.append)
    tc = TestClient(module.app, raise_server_exceptions=False)
    assert tc.get("/orders/1/discounted?code=BOGUS").status_code == 500
    sys.path.remove(str(root))

    outcome = heal(events[-1], "shop_service.main:app", str(root),
                   complete=lambda _e: GOOD_DIFF, suite_path=None)
    assert outcome.status == "deployed"

    main = ServiceProcess(str(root), "shop_service.main:app", free_port(),
                          "s1", PLATFORM_ROOT)
    main.start()
    proxy = TrafficSplitProxy(f"http://127.0.0.1:{main.port}", free_port())
    proxy.start()

    yield {"root": str(root), "main": main, "proxy": proxy,
           "record": outcome.verification_record,
           "patched_tree": outcome.patched_tree}
    proxy.stop()
    main.stop()


def make_traffic(proxy_port: int, n: int = 40):
    """A realistic mix: mostly healthy requests, some hitting the fault."""
    def drive():
        with httpx.Client(timeout=5.0) as c:
            for i in range(n):
                code = "BOGUS" if i % 4 == 0 else "SAVE10"
                c.get(f"http://127.0.0.1:{proxy_port}/orders/1/discounted",
                      params={"code": code})
    return drive


def test_good_patch_is_promoted_and_live_service_heals(live):
    proxy, main = live["proxy"], live["main"]
    controller = LiveCanaryController(main, proxy, PLATFORM_ROOT)
    report = controller.run(live["record"], live["patched_tree"],
                            "shop_service.main:app",
                            make_traffic(proxy.port), fraction=0.5)

    assert report.promoted, report.decision
    # baseline saw the fault; the canary window did not
    assert report.baseline.error_rate > 0.15
    assert report.canary.error_rate == 0.0
    assert report.canary.requests > 0

    # the LIVE service on its original port now serves the failing request
    with httpx.Client(timeout=5.0) as c:
        r = c.get(f"http://127.0.0.1:{main.port}/orders/1/discounted",
                  params={"code": "BOGUS"})
        assert r.status_code == 200
        assert r.json()["discounted"] == 100.0
        r2 = c.get(f"http://127.0.0.1:{main.port}/orders/1/discounted",
                   params={"code": "SAVE10"})
        assert r2.json()["discounted"] == 90.0


def test_bad_canary_breaches_and_live_service_is_untouched(live, tmp_path):
    """A canary that fails MORE than baseline must breach the guard, and the
    live tree must remain exactly as it was."""
    proxy, main = live["proxy"], live["main"]

    bad_tree = tmp_path / "bad"
    shutil.copytree(live["root"], bad_tree)
    m = bad_tree / "shop_service" / "main.py"
    m.write_text(m.read_text().replace(
        'order = ORDERS[oid]',
        'raise RuntimeError("regression")\n    order = ORDERS[oid]'))

    before = (tmp_path / ".." ).resolve()  # unused; keep flake quiet
    live_main_src = open(os.path.join(live["root"], "shop_service/main.py")).read()

    controller = LiveCanaryController(main, proxy, PLATFORM_ROOT)
    report = controller.run(live["record"], str(bad_tree),
                            "shop_service.main:app",
                            make_traffic(proxy.port), fraction=0.5)

    assert not report.promoted
    assert any("error rate" in b for b in report.decision["breaches"])
    # live tree untouched, service still serving (with its original fault)
    assert open(os.path.join(live["root"], "shop_service/main.py")).read() == live_main_src
    with httpx.Client(timeout=5.0) as c:
        assert c.get(f"http://127.0.0.1:{main.port}/orders/1/discounted",
                     params={"code": "SAVE10"}).status_code == 200
