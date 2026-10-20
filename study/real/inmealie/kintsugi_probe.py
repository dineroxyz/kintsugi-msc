"""pytest plugin: install Kintsugi into Mealie for one test run."""
import os


def pytest_sessionstart(session):
    sink = os.environ.get("KINTSUGI_SINK")
    if not sink:
        return
    from mealie.app import app
    from kintsugi_mealie import install
    install(app, sink, os.environ.get("KINTSUGI_SNAPDIR"))
