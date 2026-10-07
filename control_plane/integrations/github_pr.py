"""Record a repair as a GitHub pull request, the way a team would review it.

For each live repair:
  incident/<run>   a branch holding the release with the bug, as it ran
  kintsugi/fix-<run>  a branch with the admitted patch and the regression
                   test compiled from the failure
  a pull request from the fix branch into the incident branch, carrying the
  evidence: the reproduction, the gate's checks, the timing, the certificate.

Configured by KINTSUGI_GITHUB_TOKEN (a token allowed to write contents and
pull requests on one repository) and KINTSUGI_GITHUB_REPO ("owner/name").
Without both, nothing happens. Files live under services/ in the repository.
"""

from __future__ import annotations

import base64
import os

import httpx

API = "https://api.github.com"


def configured() -> bool:
    return bool(os.environ.get("KINTSUGI_GITHUB_TOKEN") and
                os.environ.get("KINTSUGI_GITHUB_REPO"))


def _client() -> httpx.Client:
    return httpx.Client(base_url=API, timeout=20.0, headers={
        "Authorization": f"Bearer {os.environ['KINTSUGI_GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "kintsugi"})


def _repo() -> str:
    return os.environ["KINTSUGI_GITHUB_REPO"]


def _branch(c: httpx.Client, name: str, from_sha: str) -> None:
    r = c.post(f"/repos/{_repo()}/git/refs",
               json={"ref": f"refs/heads/{name}", "sha": from_sha})
    r.raise_for_status()


def _head(c: httpx.Client, branch: str) -> str:
    r = c.get(f"/repos/{_repo()}/git/ref/heads/{branch}")
    r.raise_for_status()
    return r.json()["object"]["sha"]


def _put(c: httpx.Client, path: str, text: str, branch: str, message: str) -> None:
    existing = c.get(f"/repos/{_repo()}/contents/{path}", params={"ref": branch})
    body = {"message": message, "branch": branch,
            "content": base64.b64encode(text.encode()).decode()}
    if existing.status_code == 200:
        body["sha"] = existing.json()["sha"]
    r = c.put(f"/repos/{_repo()}/contents/{path}", json=body)
    r.raise_for_status()


def open_repair_pr(*, run: str, entry_id: str, title: str, buggy: dict[str, str],
                   fixed: dict[str, str], regression_test: str | None,
                   evidence: dict) -> dict:
    """Open the pull request; returns {"url", "number"}. `buggy` and `fixed`
    map paths under services/ to file contents."""
    with _client() as c:
        base = _head(c, "main")
        incident, fix = f"incident/{run}", f"kintsugi/fix-{run}"
        _branch(c, incident, base)
        for path, text in buggy.items():
            _put(c, f"services/{path}", text, incident,
                 f"Release with bug {entry_id} planted (Kintsugi live run {run})")
        _branch(c, fix, _head(c, incident))
        for path, text in fixed.items():
            _put(c, f"services/{path}", text, fix,
                 f"Fix {evidence.get('signature', entry_id)}, admitted by the gate")
        if regression_test:
            _put(c, f"tests/test_kintsugi_{entry_id.lower()}_{run}.py", regression_test,
                 fix, "Regression test compiled from the captured failure")
        checks = "\n".join(f"- {'passed' if ok else 'failed'}: {name}"
                           for name, ok in evidence.get("conditions", {}).items())
        note = evidence.get("explanation")
        note_md = (f"\n**In plain English** (written by a small AI model; not verified)\n> {note}\n"
                   if note else "")
        body = f"""Kintsugi repaired this bug during a live run and recorded the change here for review.

**Bug {entry_id}:** {title}
{note_md}
**What happened**
- Failure caught: `{evidence.get('request', '')}` raised `{evidence.get('exception', '')}`
- Reproduced in a sealed copy: {evidence.get('replays', '')}
- Patch attempts: {evidence.get('attempts', '')} (admitted on the last)
- Recovered {evidence.get('mttr', '?')} s after the first error

**The gate's checks on the admitted patch**
{checks}

**Certificate** {evidence.get('certificate_url', '')}
Fingerprint `{evidence.get('certificate_hash', '')[:16]}`

This branch merges into `{incident}`, which holds the release with the bug exactly as it ran.
The test added here fails on that release and passes with this fix.

Written by Kintsugi (model {evidence.get('model', '')}). Nobody wrote this patch by hand: review it like any other change."""
        r = c.post(f"/repos/{_repo()}/pulls", json={
            "title": f"Kintsugi: fix {entry_id}, {title}"[:240],
            "head": fix, "base": incident, "body": body})
        r.raise_for_status()
        pr = r.json()
        return {"url": pr["html_url"], "number": pr["number"]}
