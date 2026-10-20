"""Prefect: where the server's SQLite database is, for snapshots."""


def db_path():
    from prefect.settings import PREFECT_SERVER_DATABASE_CONNECTION_URL
    url = PREFECT_SERVER_DATABASE_CONNECTION_URL.value() or ""
    return url.split("///", 1)[1] if url.startswith("sqlite") and "///" in url else None
