"""Phase 2 exit criteria, as tests.

Ten deterministic seeded faults, captured live, must reproduce with a stable
signature. Two non-deterministic faults (a coin-flip failure and a shared-state
race) must be REFUSED by the determinism checker. Reproduction must be
hermetic: it runs with the upstream dependency absent, served entirely from
recorded responses, and a dependence on unrecorded state is a refusal.
The compiled test must fail while the fault is present and pass when fixed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import httpx
import pytest
from fastapi.testclient import TestClient

from control_plane.reproduction.engine import reproduce

FAULTY_SERVICE = textwrap.dedent('''
    """Test fixture: a service with ten deterministic seeded faults and two
    non-deterministic ones, each behind its own endpoint."""
    import random
    import threading

    from fastapi import FastAPI

    from libs.kintsugi_capture import CaptureClient, CaptureMiddleware

    app = FastAPI()
    app.add_middleware(CaptureMiddleware, service="faulty", release="f1")

    pricing = CaptureClient()

    ORDERS = {1: {"id": 1, "total": 100.0}}

    # --- ten deterministic faults -------------------------------------
    @app.get("/f1/{oid}")
    def f1(oid: int):
        return ORDERS[oid]                       # KeyError on unknown id

    @app.get("/f2")
    def f2(code: str = ""):
        rate = {"A": 0.1}.get(code)
        return {"v": 100 * (1 - rate)}           # TypeError on unknown code

    @app.get("/f3")
    def f3(n: int = 0):
        return {"v": 100 / n}                    # ZeroDivisionError

    @app.get("/f4")
    def f4():
        return {"v": int("not-a-number")}        # ValueError

    @app.get("/f5")
    def f5():
        return {"v": [1, 2, 3][10]}              # IndexError

    @app.get("/f6")
    def f6():
        x = None
        return {"v": x.upper()}                  # AttributeError

    @app.get("/f7")
    def f7():
        return {"v": "a" + 1}                    # TypeError, distinct frame

    @app.get("/f8")
    def f8():
        import nonexistent_module                # ModuleNotFoundError
        return {}

    @app.get("/f9")
    def f9():
        r = pricing.get("http://beta/price/widget")
        price = r.json()["price"]
        return {"v": price * ORDERS[2]["total"]} # upstream call, THEN KeyError

    @app.get("/f10")
    def f10():
        assert False, "invariant violated"       # AssertionError

    # --- two non-deterministic faults ---------------------------------
    @app.get("/flaky")
    def flaky():
        if random.random() < 0.6:
            raise RuntimeError("coin flip")
        return {"ok": True}

    _counter = {"v": 0}

    @app.get("/race")
    def race():
        # unsynchronised read-modify-write across threads; fails only when
        # interleaving is unlucky
        threads = [threading.Thread(target=lambda: _counter.update(
            v=_counter["v"] + 1)) for _ in range(8)]
        before = _counter["v"]
        for t in threads: t.start()
        for t in threads: t.join()
        if _counter["v"] != before + 8 or random.random() < 0.5:
            raise RuntimeError("lost update")
        return {"ok": True}
''')

DETERMINISTIC = [
    ("/f1/999", "KeyError"), ("/f2?code=BOGUS", "TypeError"),
    ("/f3?n=0", "ZeroDivisionError"), ("/f4", "ValueError"),
    ("/f5", "IndexError"), ("/f6", "AttributeError"),
    ("/f7", "TypeError"), ("/f8", "ModuleNotFoundError"),
    ("/f9", "KeyError"), ("/f10", "AssertionError"),
]


@pytest.fixture(scope="module")
def fixture_pkg(tmp_path_factory):
    """Write the faulty service where both this process and harness
    subprocesses can import it."""
    root = tmp_path_factory.mktemp("faulty_root")
    pkg = root / "faulty_service"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text(FAULTY_SERVICE)
    sys.path.insert(0, str(root))
    yield str(root)
    sys.path.remove(str(root))


def capture_from(fixture_pkg: str, path: str) -> dict:
    """Drive the faulty service live and return the flushed capture event."""
    events: list[dict] = []
    import importlib
    module = importlib.import_module("faulty_service.main")
    importlib.reload(module)

    # rebuild middleware stack with a synchronous in-test deliver
    from libs.kintsugi_capture import CaptureMiddleware
    module.app.user_middleware.clear()
    module.app.middleware_stack = None
    module.app.add_middleware(CaptureMiddleware, service="faulty", release="f1",
                              deliver=events.append)
    module.pricing._transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"price": 2.5}))

    client = TestClient(module.app, raise_server_exceptions=False)
    resp = client.get(path)
    assert resp.status_code == 500, f"{path} did not fail live"
    assert events, f"{path} produced no capture"
    return events[-1]


def test_ten_deterministic_faults_reproduce(fixture_pkg):
    for path, exc_type in DETERMINISTIC:
        capture = capture_from(fixture_pkg, path)
        result = reproduce(capture, "faulty_service.main:app",
                           workdir=fixture_pkg, runs=5)
        assert result.reproduced, f"{path}: {result.reason}"
        assert result.signature.startswith(exc_type + "@"), \
            f"{path}: signature {result.signature}"


def test_flaky_fault_is_refused(fixture_pkg):
    # provoke until it fails once so we have a capture to replay
    for _ in range(50):
        try:
            capture = capture_from(fixture_pkg, "/flaky")
            break
        except AssertionError:
            continue
    result = reproduce(capture, "faulty_service.main:app",
                       workdir=fixture_pkg, runs=5)
    assert not result.reproduced
    assert "non-deterministic" in result.reason or "did not recur" in result.reason


def test_race_fault_is_refused(fixture_pkg):
    for _ in range(50):
        try:
            capture = capture_from(fixture_pkg, "/race")
            break
        except AssertionError:
            continue
    # the fault fires on a coin flip, so all five replays CAN fail
    # identically by luck (~3%); one retry brings the flake to ~0.1%
    for attempt in range(2):
        result = reproduce(capture, "faulty_service.main:app",
                           workdir=fixture_pkg, runs=5)
        if not result.reproduced:
            break
    assert not result.reproduced


def test_reproduction_is_hermetic(fixture_pkg):
    """f9 calls an upstream service that does not exist during replay; the
    recorded response must serve it. If replay silently used the network this
    test could not pass, and if the capture lacked the call the engine must
    refuse rather than guess."""
    capture = capture_from(fixture_pkg, "/f9")
    assert capture["external_calls"], "outbound call was not recorded"
    result = reproduce(capture, "faulty_service.main:app",
                       workdir=fixture_pkg, runs=5)
    assert result.reproduced
    assert result.signature.startswith("KeyError@")

    # now strip the recorded call: dependence on unrecorded state = refusal
    stripped = dict(capture, external_calls=[])
    result2 = reproduce(stripped, "faulty_service.main:app",
                        workdir=fixture_pkg, runs=5)
    assert not result2.reproduced
    assert "outbound call" in result2.reason


def test_compiled_test_fails_on_faulty_and_passes_on_fixed(fixture_pkg, tmp_path):
    from control_plane.reproduction.compiler import compile_reproduction

    capture = capture_from(fixture_pkg, "/f2?code=BOGUS")
    result = reproduce(capture, "faulty_service.main:app",
                       workdir=fixture_pkg, runs=5)
    assert result.reproduced

    test_src = compile_reproduction(capture, "faulty_service.main:app",
                                    result.signature, incident_id="42")
    test_file = tmp_path / "test_incident_42.py"
    test_file.write_text(test_src)

    def run_pytest(extra_path):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [extra_path, os.getcwd(), env.get("PYTHONPATH", "")])
        return subprocess.run([sys.executable, "-m", "pytest", str(test_file), "-q"],
                              capture_output=True, text=True, env=env)

    # against the faulty tree: the compiled test must FAIL
    proc = run_pytest(fixture_pkg)
    assert proc.returncode != 0, "compiled test passed against the faulty code"
    assert "has recurred" in proc.stdout

    # against a fixed tree: it must PASS
    fixed_root = tmp_path / "fixed_root"
    import shutil
    shutil.copytree(os.path.join(fixture_pkg), fixed_root, dirs_exist_ok=True)
    main = fixed_root / "faulty_service" / "main.py"
    main.write_text(main.read_text().replace(
        'rate = {"A": 0.1}.get(code)',
        'rate = {"A": 0.1}.get(code, 0.0)'))
    proc = run_pytest(str(fixed_root))
    assert proc.returncode == 0, proc.stdout[-1500:]


def test_shadow_worktree_provisions_the_release(tmp_path):
    """The provisioner must yield the code as of the release commit, not the
    current tree, and must refuse unknown releases."""
    from control_plane.reproduction.provisioner import ProvisionError, shadow_worktree

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "svc.py").write_text("VERSION = 'v1'\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "v1"], check=True)
    v1 = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                        capture_output=True, text=True).stdout.strip()
    (repo / "svc.py").write_text("VERSION = 'v2'\n")
    subprocess.run(["git", "-C", str(repo), "commit", "-aqm", "v2"], check=True)

    with shadow_worktree(str(repo), v1) as tree:
        assert "v1" in (pytest.importorskip("pathlib").Path(tree) / "svc.py").read_text()
    # worktree cleaned up
    assert not os.path.exists(tree)

    with pytest.raises(ProvisionError):
        with shadow_worktree(str(repo), "no-such-release"):
            pass
