"""Failing-test healing reports the Live view's events when asked to."""

import os
import shutil
import subprocess
import sys
import tempfile

from control_plane.testheal import Usage, heal

APP = "def add(a, b):\n    return a - b\n"
TEST = "from pkg.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
FIX = """--- a/pkg/calc.py
+++ b/pkg/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""


class _Stream:
    def __init__(self, text):
        self.text = text
        self.text_stream = iter([text[:10], text[10:]])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        class U:
            input_tokens = output_tokens = 0

        class B:
            type = "text"
            text = self.text

        class M:
            usage, stop_reason, content = U(), "end_turn", [B()]
        return M()


class _Client:
    class messages:
        @staticmethod
        def stream(**kw):
            return _Stream("pkg/calc.py" if "locate" in kw.get("system", "") else FIX)


def test_failing_test_heal_emits_live_events():
    src = tempfile.mkdtemp()
    os.makedirs(os.path.join(src, "pkg"))
    os.makedirs(os.path.join(src, "tests"))
    open(os.path.join(src, "pkg", "__init__.py"), "w").close()
    open(os.path.join(src, "pkg", "calc.py"), "w").write(APP)
    open(os.path.join(src, "tests", "test_calc.py"), "w").write(TEST)

    def fresh(name):
        d = tempfile.mkdtemp() + "/t"
        shutil.copytree(src, d)
        return d

    def run(tree, sel):
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *sel],
                           cwd=tree, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": tree})
        return r.returncode, r.stdout + r.stderr

    events = []
    res = heal(run_tests=run, fresh_tree=fresh, drop_tree=lambda p: shutil.rmtree(p, ignore_errors=True),
               selectors=["tests/test_calc.py"], test_files=["tests/test_calc.py"], suite=["tests"],
               package="pkg", client=_Client(), usage=Usage(), budget=5.0,
               on_event=lambda kind, **d: events.append((kind, d)))
    assert res.healed, res.reason
    kinds = [k for k, _ in events]
    for k in ("reproduce_start", "reproduce_done", "context", "candidate_start", "token", "gate_result"):
        assert k in kinds
    gate = [d for k, d in events if k == "gate_result"][-1]
    assert gate["admitted"] and all(gate["conditions"].values())
    assert set(gate["conditions"]) >= {"applies", "structural", "security", "reproduction", "suite"}
