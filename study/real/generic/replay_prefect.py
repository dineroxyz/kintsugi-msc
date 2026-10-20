"""One replay of a captured Prefect server failure, in a fresh process.

    <prefect python> replay_prefect.py capture.json request|snapshot
Run with the tree under test as the working directory. Prints one JSON verdict.
"""
import json
import os
import shutil
import sys
import tempfile

capture = json.load(open(sys.argv[1]))
mode = sys.argv[2]
home = tempfile.mkdtemp(prefix="kintsugi-prefect-")
db = os.path.join(home, "prefect.db")
if mode == "snapshot" and capture.get("snapshot") and os.path.exists(capture["snapshot"]):
    shutil.copy(capture["snapshot"], db)
sink = os.path.join(home, "sink.jsonl")
os.environ.update({"PREFECT_HOME": home, "KINTSUGI_SINK": sink,
                   "PREFECT_SERVER_DATABASE_CONNECTION_URL": f"sqlite+aiosqlite:///{db}",
                   "PREFECT_SERVER_ANALYTICS_ENABLED": "false",
                   "PREFECT_SERVER_DOCKET_NAME": "kintsugi-replay"})
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))
import kintsugi_generic  # noqa: E402,F401  (wraps every Starlette app)
from prefect.server.api.server import create_app  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

req = capture["request"]
headers = {k: v for k, v in (req.get("headers") or {}).items()
           if v != "[redacted]" and k.lower() not in ("host", "content-length")}
client = TestClient(create_app(ephemeral=True), raise_server_exceptions=False, base_url="https://test")
client.__enter__()                       # start-up runs the database migrations
url = req["path"] + (f"?{req['query']}" if req.get("query") else "")
resp = client.request(req["method"], url, headers=headers, content=(req.get("body") or "").encode())
try:
    client.__exit__(None, None, None)    # some background services object to a short life
except Exception:
    pass

events = [json.loads(l) for l in open(sink)] if os.path.exists(sink) else []


def frame(frames):
    app = [f for f in frames if "/site-packages/" not in f["file"] and "/lib/python" not in f["file"]
           and "/study/real/generic/" not in f["file"]]
    f = (app or frames)[-1]
    return f"{os.path.basename(f['file'])}:{f['line']}"


print(json.dumps({"failed": bool(events), "status_code": resp.status_code,
                  "exception_type": events[-1]["exception_type"] if events else None,
                  "top_frame": frame(events[-1]["frames"]) if events else None}))
shutil.rmtree(home, ignore_errors=True)
