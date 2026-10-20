"""Turn the blinded correctness sheet into a printable rater pack (PDF).

    python study/build_rater_pack.py   -> study/main/correctness/Rater_Pack_{A,B}.pdf
Each rater gets the same blinded items and their own answer table. The key
(key.json) is NOT included.
"""
import html
import os
import re
import subprocess

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main", "correctness")
BRAVE = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"


def md_to_html(md: str) -> str:
    out, code, buf = [], False, []
    for line in md.split("\n"):
        if line.startswith("```"):
            if code:
                rows = []
                for l in buf:
                    cls = "add" if l.startswith("+") and not l.startswith("+++") else \
                          "del" if l.startswith("-") and not l.startswith("---") else ""
                    rows.append(f"<span class=\"{cls} ln\">{html.escape(l) or ' '}</span>")
                out.append("<pre>" + "".join(rows) + "</pre>")
                buf, code = [], False
            else:
                code = True
            continue
        if code:
            buf.append(line)
            continue
        t = html.escape(line)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
        if line.startswith("## "):
            out.append(f'<h2 class="item">{t[3:]}</h2>')
        elif line.startswith("# "):
            continue
        elif line.startswith("- "):
            out.append(f"<li>{t[2:]}</li>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.strip():
            out.append(f"<p>{t}</p>")
    return "\n".join(out)


def main():
    md = open(os.path.join(HERE, "sheet.md")).read()
    codes = re.findall(r"^## (P\d+)", md, re.M)
    body = md_to_html(md.split("---", 1)[1])
    for rater, who in (("A", "Rater A"), ("B", "Rater B")):
        rows = "".join(f"<tr><td><b>{c}</b></td><td></td><td></td></tr>" for c in codes)
        page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Rater Pack {rater}</title>
<style>
@page {{ size: A4; margin: 15mm; }}
body {{ font: 11pt/1.55 -apple-system, Helvetica, Arial, sans-serif; color:#111; }}
h1 {{ font-size: 22pt; margin: 0 0 4px; }} h2 {{ font-size: 15pt; margin: 18px 0 6px; }}
h2.item {{ break-before: page; background:#f3efe6; padding:6px 10px; border-left:5px solid #8a6a1f; }}
pre {{ font: 9pt/1.4 Menlo, monospace; background:#f7f7f7; border:1px solid #ddd; padding:8px;
       white-space: pre-wrap; word-break: break-word; }}
.ln {{ display:block; }} .add {{ background:#e3f5e8; }} .del {{ background:#fbe4e4; display:block; }}
code {{ font: 9.5pt Menlo, monospace; background:#f2f2f2; padding:0 3px; }}
table {{ width:100%; border-collapse:collapse; }} td, th {{ border:1px solid #999; padding:6px 9px; text-align:left; }}
th {{ background:#eee; }} .box {{ border:2px solid #8a6a1f; padding:10px 14px; margin:10px 0; }}
</style></head><body>
<h1>Patch correctness: rater pack</h1>
<p><b>{who}.</b> Kintsugi MSc project, Abe Oluwaseyi Elizabeth (259074235). Supervisor: Dr A.U Rufai.</p>
<div class="box">
<p><b>What you are asked to do.</b> There are {len(codes)} items. Each shows a failure in a small web service,
a <b>patch</b> that was put live to fix it, and the <b>reference fix</b> written by the person who planted the bug.
For each item, decide whether the patch is a correct fix, and write one word in the table below.</p>
<ul>
<li><b>correct</b>: fixes the failure and breaks nothing else (the same as, or as good as, the reference)</li>
<li><b>partial</b>: fixes the failure but is incomplete or narrower than the reference</li>
<li><b>incorrect</b>: does not fix the failure, or changes other behaviour wrongly</li>
</ul>
<p>You are <b>not told which approach</b> produced each patch, and the items are in random order. Judge on the code alone.
Work on your own: do not discuss items with the other rater until you have both finished.
Green lines are added code, red lines are removed code. It takes about one hour.</p>
</div>
<h2>Your answers</h2>
<table><tr><th style="width:16%">Item</th><th style="width:24%">Verdict</th><th>Note (optional)</th></tr>{rows}</table>
<p>Name: ______________________ &nbsp;&nbsp; Date: ____________ &nbsp;&nbsp; Signature: ______________</p>
{body}
</body></html>"""
        src = os.path.join(HERE, f"rater_pack_{rater}.html")
        open(src, "w").write(page)
        pdf = os.path.join(HERE, f"Rater_Pack_{rater}.pdf")
        subprocess.run([BRAVE, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", "file://" + src], capture_output=True)
        print(pdf)


if __name__ == "__main__":
    main()
