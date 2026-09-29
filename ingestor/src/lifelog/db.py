import time
from collections.abc import Callable
from pathlib import Path

import psycopg
from psycopg import sql

SCHEMA_LOCK_KEY = 4_242_000  # distinct from the sync lock (4_242_001)


def connect(url: str) -> psycopg.Connection:
    """Autocommit connection: every multi-statement unit of work uses an explicit conn.transaction()."""
    return psycopg.connect(url, autocommit=True, connect_timeout=5)


def wait_for_db(
    url: str, timeout_seconds: float = 60, sleep: Callable[[float], None] = time.sleep
) -> psycopg.Connection:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            return connect(url)
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            sleep(1)


def migrate(conn: psycopg.Connection, db_dir: Path) -> list[str]:
    """Apply db/migrations/*.sql not yet applied, in filename order. Returns the newly applied names.

    Files must not contain their own BEGIN/COMMIT.
    """
    conn.execute("CREATE SCHEMA IF NOT EXISTS ops")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.schema_migration (
            filename    text PRIMARY KEY,
            applied_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    applied = {row[0] for row in conn.execute("SELECT filename FROM ops.schema_migration")}
    newly_applied = []
    for path in sorted((Path(db_dir) / "migrations").glob("*.sql")):
        if path.name in applied:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8-sig"))
            conn.execute("INSERT INTO ops.schema_migration (filename) VALUES (%s)", (path.name,))
        newly_applied.append(path.name)
    return newly_applied


def rebuild_mart(conn: psycopg.Connection, db_dir: Path, reader_role: str = "grafana_ro") -> None:
    """Drop and recreate the mart schema from db/views/*.sql. Safe: mart holds only views."""
    reader = sql.Identifier(reader_role)
    with conn.transaction():
        conn.execute("SET LOCAL lock_timeout = '10s'")
        conn.execute("DROP SCHEMA IF EXISTS mart CASCADE")
        conn.execute("CREATE SCHEMA mart")
        for path in sorted((Path(db_dir) / "views").glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8-sig"))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA mart TO {}").format(reader))
        conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA mart TO {}").format(reader))


def setup(conn: psycopg.Connection, db_dir: Path) -> list[str]:
    """Startup: drop mart, apply migrations, rebuild mart, all under an advisory lock.

    Mart goes first so that a migration can alter columns the views depend on.
    Returns the newly applied migration names.
    """
    conn.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_LOCK_KEY,))
    try:
        conn.execute("DROP SCHEMA IF EXISTS mart CASCADE")
        applied = migrate(conn, db_dir)
        rebuild_mart(conn, db_dir)
        return applied
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_LOCK_KEY,))
