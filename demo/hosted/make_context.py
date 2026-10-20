"""Assemble a hosted demo's build context: the real app at its current
release, the real past defect, the Kintsugi pieces, and the supervisor.

    python demo/hosted/make_context.py prefect /path/to/context
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KINTSUGI = os.path.dirname(os.path.dirname(HERE))
REAL = os.path.expanduser("~/kintsugi-real")


def main(app: str, out: str) -> None:
    cfg = json.load(open(os.path.join(HERE, "apps", f"{app}.json")))
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    src = os.path.join(REAL, cfg["repo"])
    archive = subprocess.run(["git", "-C", src, "archive", "HEAD"], capture_output=True, check=True).stdout
    os.makedirs(os.path.join(out, "pristine"))
    subprocess.run(["tar", "-x", "-C", os.path.join(out, "pristine")], input=archive, check=True)
    for f in cfg.get("generated", []):
        shutil.copy(os.path.join(src, f), os.path.join(out, "pristine", f))
    cands = os.path.join(KINTSUGI, "study", "real", "apps", f"{cfg['repo']}.candidates.jsonl")
    if cfg["repo"] == "mealie":
        cands = os.path.join(KINTSUGI, "study", "real", "candidates.jsonl")
    fault = next(json.loads(l)["fault_diff"] for l in open(cands) if json.loads(l)["sha"] == cfg["sha"])
    if cfg.get("fault_files"):                    # keep only the named files' parts of the defect
        parts = ["diff --git" + p for p in fault.split("diff --git")[1:]]
        fault = "".join(p for p in parts if any(f" a/{f} " in p.splitlines()[0] for f in cfg["fault_files"]))
    open(os.path.join(out, "fault.diff"), "w").write(fault)
    json.dump(cfg, open(os.path.join(out, "demo.json"), "w"), indent=1)
    k = os.path.join(out, "kintsugi")
    for part in ("control_plane", "libs", "study/real/generic"):
        shutil.copytree(os.path.join(KINTSUGI, part), os.path.join(k, part),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "drills"))
    shutil.copy(os.path.join(HERE, "supervisor.py"), os.path.join(out, "supervisor.py"))
    shutil.copy(os.path.join(HERE, f"Dockerfile.{cfg.get('dockerfile', app)}"), os.path.join(out, "Dockerfile"))
    print(out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
