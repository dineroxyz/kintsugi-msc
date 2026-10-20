"""pytest plugin for lnbits: start the app (which runs its migrations) for
every selected test, as a full-suite run does, so a single test file can run
on its own."""


def pytest_collection_modifyitems(session, config, items):
    for item in items:
        names = getattr(item, "fixturenames", None)
        if names is not None and "app" not in names:
            names.insert(0, "app")
