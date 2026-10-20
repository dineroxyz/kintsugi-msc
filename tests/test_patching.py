"""The loose applier: informal hunks applied by content, canonical diff out."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from control_plane.patching import apply_loose


def _repo(tmp_path, text):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    (tmp_path / "m.py").write_text(text)
    subprocess.run(["git", "add", "."], cwd=tmp_path)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], cwd=tmp_path)


def test_bare_hunk_header_is_applied_by_content(tmp_path):
    _repo(tmp_path, "def f(ret, t):\n    if t:\n        return ret[t]\n    return ret\n")
    loose = "--- a/m.py\n+++ b/m.py\n@@\n     if t:\n-        return ret[t]\n+        return ret.get(t, [])\n     return ret\n"
    canonical, err = apply_loose(str(tmp_path), loose)
    assert err == "" and "@@ -1,4 +1,4 @@" in canonical
    assert "ret.get(t, [])" in (tmp_path / "m.py").read_text()


def test_ambiguous_or_missing_context_is_refused(tmp_path):
    _repo(tmp_path, "x = 1\nx = 1\n")
    canonical, err = apply_loose(str(tmp_path), "--- a/m.py\n+++ b/m.py\n@@\n-x = 1\n+x = 2\n")
    assert canonical is None and "exactly once" in err
