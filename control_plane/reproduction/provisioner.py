"""Shadow provisioner: a checkout of the service tree at the incident's release.

The release identifier captured with every incident is a git commit. The
provisioner materialises that commit as a detached worktree so the harness
replays against the code that was actually serving, not whatever the working
tree has drifted to since. Worktrees are cheap, shared-object checkouts;
one per reproduction, removed afterwards.
"""

from __future__ import annotations

import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path


class ProvisionError(RuntimeError):
    pass


@contextmanager
def shadow_worktree(repo_root: str, release: str):
    """Yield a directory containing `repo_root` checked out at `release`.

    Falls back is deliberately absent: an unknown release is an error, not a
    silent replay against the wrong code. The caller records the release in
    the certificate either way.
    """
    tmp = tempfile.mkdtemp(prefix="kintsugi-shadow-")
    path = str(Path(tmp) / "tree")
    add = subprocess.run(
        ["git", "-C", repo_root, "worktree", "add", "--detach", path, release],
        capture_output=True, text=True)
    if add.returncode != 0:
        raise ProvisionError(
            f"cannot provision release {release!r}: {add.stderr.strip()}")
    try:
        yield path
    finally:
        subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", path],
                       capture_output=True)
