# Runs at start-up of every Python process launched with this folder on
# PYTHONPATH (including servers that tests start in subprocesses). It imports
# nothing up front: it waits until the program itself imports Starlette, then
# attaches Kintsugi's capture. Inert unless KINTSUGI_SINK is set.
import os
import sys

if os.environ.get("KINTSUGI_SINK"):
    import importlib.abc
    import importlib.machinery

    class _After(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name != "starlette.applications":
                return None
            for finder in sys.meta_path:
                if finder is self:
                    continue
                spec = getattr(finder, "find_spec", lambda *a: None)(name, path, target)
                if spec and spec.loader:
                    run = spec.loader.exec_module

                    def exec_module(module, _run=run):
                        _run(module)
                        try:
                            import kintsugi_generic
                            kintsugi_generic._install()
                        except Exception:
                            pass
                    spec.loader.exec_module = exec_module
                    return spec
            return None

    sys.meta_path.insert(0, _After())
