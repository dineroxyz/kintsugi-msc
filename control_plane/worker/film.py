"""Film a heal: one scene per step, built from what actually happened.

Each scene is rendered from the run's own evidence (the request, the replay
verdicts, the diff, the test summary) by a headless browser, and the scenes
are joined into an MP4 the owner can watch on their profile.
"""

from __future__ import annotations

import html
import os
import subprocess
import tempfile

BRAVE = os.environ.get("KINTSUGI_BROWSER",
                       "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")
W, H = 1280, 720

CSS = """
html,body{margin:0;width:1280px;height:720px;background:#17140F;color:#EDE6D8;
font-family:-apple-system,Helvetica,Arial,sans-serif;overflow:hidden}
.wrap{position:absolute;inset:0;padding:56px 72px;display:flex;flex-direction:column;gap:18px}
.step{color:#C6A25A;font-size:18px;font-weight:700}
h1{font:700 44px/1.15 Georgia,serif;margin:0}
p{font-size:21px;line-height:1.5;color:#D8CFBF;margin:0;max-width:1100px}
pre{background:#0E0C09;border:1px solid #3A3229;border-radius:8px;padding:16px 18px;margin:0;
font:15px/1.45 Menlo,monospace;color:#EDE6D8;white-space:pre-wrap;overflow:hidden;max-height:400px}
.add{color:#7FD49B}.del{color:#F08A7A}.ok{color:#7FD49B}.bad{color:#F08A7A}
.bar{position:absolute;left:72px;right:72px;bottom:36px;display:flex;justify-content:space-between;
font-size:15px;color:#8F8574}
.seam{height:3px;width:140px;background:#C6A25A}
"""


def _pre(text: str) -> str:
    rows = []
    for line in text.splitlines()[:22]:
        cls = "add" if line.startswith("+") and not line.startswith("+++") else \
              "del" if line.startswith("-") and not line.startswith("---") else ""
        rows.append(f'<span class="{cls}">{html.escape(line)}</span>')
    return "<pre>" + "\n".join(rows) + "</pre>"


def scene(step: str, title: str, body: str = "", code: str = "", footer: str = "") -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
            f"<div class='wrap'><div class='step'>{html.escape(step)}</div><div class='seam'></div>"
            f"<h1>{html.escape(title)}</h1>{f'<p>{body}</p>' if body else ''}"
            f"{_pre(code) if code else ''}</div>"
            f"<div class='bar'><span>Kintsugi</span><span>{html.escape(footer)}</span></div></body></html>")


def render(scenes: list[tuple[str, float]], out_mp4: str) -> str:
    """scenes: (html, seconds). Returns the MP4 path."""
    work = tempfile.mkdtemp(prefix="kintsugi-film-")
    listing = []
    for i, (page, secs) in enumerate(scenes):
        src = os.path.join(work, f"s{i:02d}.html")
        png = os.path.join(work, f"s{i:02d}.png")
        open(src, "w").write(page)
        subprocess.run([BRAVE, "--headless", "--disable-gpu", "--hide-scrollbars",
                        f"--window-size={W},{H}", f"--screenshot={png}", "file://" + src],
                       capture_output=True, timeout=60)
        listing.append(f"file '{png}'\nduration {secs}")
    listing.append(f"file '{os.path.join(work, f's{len(scenes) - 1:02d}.png')}'")
    lst = os.path.join(work, "list.txt")
    open(lst, "w").write("\n".join(listing) + "\n")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                    "-vf", "fps=25,format=yuv420p", "-c:v", "libx264", "-movflags", "+faststart",
                    out_mp4], check=True, timeout=300)
    return out_mp4
