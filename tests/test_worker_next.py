"""The Next.js worker's gate rules and evidence handling (no app, no model)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from control_plane.worker.fixer import structural
from control_plane.worker.nextjs import top_frame

GOOD = """--- a/src/app/api/img/[id]/route.ts
+++ b/src/app/api/img/[id]/route.ts
@@ -10 +10 @@
-  if (row.private) return new NextResponse("Not found", { status: 404 });
+  if (!row || row.private) return new NextResponse("Not found", { status: 404 });
"""


def test_a_real_fix_passes_the_structural_check():
    assert structural(GOOD) == []


def test_structural_check_rejects_the_usual_cheats():
    assert structural(GOOD.replace("route.ts", "route.test.ts"))
    assert structural(GOOD + "+  // @ts-ignore\n")
    assert structural(GOOD + "+  } catch {}\n")
    assert structural(GOOD + "+  } catch (e) { return NextResponse.json({ ok: true }) }\n")


def test_top_frame_prefers_the_apps_own_source():
    frames = [{"file": "/x/.next/dev/server/chunks/a.js", "line": 128,
               "src": "src/app/api/img/[id]/route.ts", "src_line": 10},
              {"file": "node:internal/process/task_queues", "line": 104}]
    assert top_frame(frames, "/x") == "src/app/api/img/[id]/route.ts:10"
