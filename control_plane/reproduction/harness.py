"""Replay harness: perform ONE replay of a captured failure and report.

Runs as its own process, one process per replay, so that consecutive replays
share no interpreter state: module-level caches, random seeds and leaked
globals from one attempt cannot influence the next. That independence is what
lets the determinism checker treat five identical outcomes as evidence.

Usage:
    python -m control_plane.reproduction.harness <capture.json> <module:attr>

The capture file is the full capture event (request + external_calls). The
harness sets replay mode, imports the service fresh, issues the captured
request against it in-process, and prints a one-line JSON verdict:

    {"failed": true, "exception_type": "...", "top_frame": "file:line",
     "status_code": 500, "hermetic_violation": false}
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile


def _signature_frame(frames: list[dict]) -> str:
    """The deepest application frame, same notion as the incident former."""
    lib_markers = ("/site-packages/", "/lib/python", "/uvicorn/", "/starlette/",
                   "/fastapi/", "/anyio/")
    app = [f for f in frames if not any(m in f["file"] for m in lib_markers)]
    f = (app or frames)[-1]
    return f"{os.path.basename(f['file'])}:{f['line']}"


def replay(capture: dict, service_spec: str) -> dict:
    """Replay one captured request against a freshly imported service."""
    from fastapi.testclient import TestClient

    sink = tempfile.NamedTemporaryFile(mode="r", suffix=".jsonl", delete=False)
    stub_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"external_calls": capture["external_calls"]}, stub_file)
    stub_file.close()

    os.environ["KINTSUGI_REPLAY_FILE"] = stub_file.name
    os.environ["KINTSUGI_CAPTURE_FILE"] = sink.name

    snap = capture.get("snapshot") or {}
    if snap.get("data") and snap.get("env"):
        # start from the data the failing request saw, in a private copy
        import base64
        import zlib
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db.write(zlib.decompress(base64.b64decode(snap["data"])))
        db.close()
        os.environ[snap["env"]] = db.name

    module_name, attr = service_spec.split(":")
    module = importlib.import_module(module_name)
    app = getattr(module, attr)

    req = capture["request"]
    url = req["path"] + (f"?{req['query']}" if req.get("query") else "")
    headers = {k: v for k, v in req.get("headers", {}).items()
               if k.lower() not in ("host", "content-length")}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.request(req["method"], url, headers=headers,
                              content=req.get("body", "").encode())

    verdict: dict = {"failed": False, "exception_type": None, "top_frame": None,
                     "status_code": response.status_code, "hermetic_violation": False}

    events = [json.loads(line) for line in open(sink.name, encoding="utf-8")
              if line.strip()]
    if events:
        event = events[-1]
        verdict["failed"] = True
        verdict["exception_type"] = event["exception_type"]
        verdict["top_frame"] = _signature_frame(event["frames"])
        verdict["hermetic_violation"] = event["exception_type"] == "HermeticViolation"

    os.unlink(sink.name)
    os.unlink(stub_file.name)
    return verdict


def main() -> None:
    capture_path, service_spec = sys.argv[1], sys.argv[2]
    with open(capture_path, encoding="utf-8") as f:
        capture = json.load(f)
    print(json.dumps(replay(capture, service_spec)))


if __name__ == "__main__":
    main()
