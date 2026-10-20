"""One replay of a captured Mealie failure, in a fresh process.

    <mealie-python> replay_mealie.py capture.json request|snapshot
Run with the tree under test as the working directory. Prints one JSON verdict.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

capture = json.load(open(sys.argv[1]))
mode = sys.argv[2]
data = tempfile.mkdtemp(prefix="kintsugi-mealie-data-")
os.environ.update({"TESTING": "True", "PRODUCTION": "True", "ALLOW_SIGNUP": "True",
                   "DATA_DIR": data})
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))

import kintsugi_mealie as K  # noqa: E402

target = K.db_path()
os.makedirs(os.path.dirname(target), exist_ok=True)
if mode == "snapshot" and capture.get("snapshot") and os.path.exists(capture["snapshot"]):
    shutil.copy(capture["snapshot"], target)
else:
    from mealie.db.init_db import main as init_db
    init_db()

from fastapi.testclient import TestClient  # noqa: E402
from mealie.app import app  # noqa: E402

events: list = []
from kintsugi_capture import CaptureMiddleware  # noqa: E402
app.add_middleware(CaptureMiddleware, service="mealie", deliver=events.append)

req = capture["request"]
headers = {k: v for k, v in req.get("headers", {}).items()
           if k.lower() not in ("host", "content-length", "authorization", "cookie")}
ident = capture.get("identity") or {}
if ident.get("sub"):
    from mealie.core.security.tokens import create_access_token
    token, _ = create_access_token({"sub": ident["sub"]})
    headers["authorization"] = f"Bearer {token}"

url = req["path"] + (f"?{req['query']}" if req.get("query") else "")
client = TestClient(app, raise_server_exceptions=False)
resp = client.request(req["method"], url, headers=headers,
                      content=req.get("body", "").encode())


def frame(frames):
    app_frames = [f for f in frames if "/site-packages/" not in f["file"]
                  and "/lib/python" not in f["file"] and "/kintsugi/libs/" not in f["file"]]
    f = (app_frames or frames)[-1]
    return f"{os.path.basename(f['file'])}:{f['line']}"


verdict = {"failed": bool(events), "status_code": resp.status_code,
           "exception_type": events[-1]["exception_type"] if events else None,
           "top_frame": frame(events[-1]["frames"]) if events else None}
shutil.rmtree(data, ignore_errors=True)
print(json.dumps(verdict))
