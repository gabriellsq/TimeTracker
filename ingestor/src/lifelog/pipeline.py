import logging
from dataclasses import dataclass, field

import psycopg

from lifelog.sources.base import ActivityIn, Source

log = logging.getLogger(__name__)

SYNC_LOCK_KEY = 4_242_001  # arbitrary, unique to this app


@dataclass
class SyncResult:
    status: str  # "success" | "failed" | "skipped"
    n_records: int = 0
    skipped_keys: list[str] = field(default_factory=list)
    error: str | None = None


def run_sync(conn: psycopg.Connection, source: Source, *, trigger: str, wait_for_lock: bool) -> SyncResult:
    """One sync run. `conn` must be in autocommit mode (see db.connect).

    Data, raw and cursor are written in ONE transaction: a failure anywhere leaves
    the database unchanged and the next run re-fetches the same changes.

    Callers must not share `conn` between concurrent runs: session advisory locks are
    re-entrant within one connection.
    """
    if wait_for_lock:
        conn.execute("SELECT pg_advisory_lock(%s)", (SYNC_LOCK_KEY,))
    elif not conn.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,)).fetchone()[0]:
        return SyncResult(status="skipped")
    try:
        run_id = _start_run(conn, source.name, trigger)
        try:
            result = _sync(conn, source)
        except Exception as exc:
            log.exception("sync of %s failed", source.name)
            result = SyncResult(status="failed", error=f"{type(exc).__name__}: {exc}")
            _best_effort(_record_failure, conn, source.name, result.error)
        _best_effort(_finish_run, conn, run_id, result)
        return result
    finally:
        if not conn.closed:
            _best_effort(conn.execute, "SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))


def close_stale_runs(conn: psycopg.Connection) -> int:
    """Mark runs left 'running' by a crashed process as failed. Call once at startup (single ingestor)."""
    return conn.execute(
        """
        UPDATE ops.sync_run
        SET status = 'failed', finished_at = now(), error = 'interrupted (process stopped mid-run)'
        WHERE status = 'running'
        """
    ).rowcount


def _best_effort(fn, *args) -> None:
    """Bookkeeping must never mask the outcome of the sync itself: log database errors and move on."""
    try:
        fn(*args)
    except psycopg.Error:
        log.exception("bookkeeping step %s failed", getattr(fn, "__name__", fn))


def _sync(conn: psycopg.Connection, source: Source) -> SyncResult:
    with conn.transaction():
        fetched = source.fetch(_read_cursor(conn, source.name))
        changed = source.upsert_raw(conn, fetched.records, force=fetched.reset)
        skipped = []
        for record in changed:
            try:
                with conn.transaction():  # savepoint: one bad record never aborts the batch
                    _upsert_activity(conn, source.to_activity(record))
            except (psycopg.OperationalError, psycopg.InterfaceError):
                raise  # infrastructure problem, not a bad record: abort, roll back, retry next run
            except Exception as exc:
                key = str(record.get("key"))
                log.warning("skipping record %s from %s: %s", key, source.name, exc)
                skipped.append(key)
        _save_cursor(conn, source.name, fetched.new_cursor)
    return SyncResult(status="success", n_records=len(changed) - len(skipped), skipped_keys=skipped)


def _upsert_activity(conn: psycopg.Connection, a: ActivityIn) -> None:
    activity_id = conn.execute(
        """
        INSERT INTO core.activity
            (source, source_id, started_at, ended_at, description, is_deleted, source_updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, source_id) DO UPDATE SET
            started_at = EXCLUDED.started_at,
            ended_at = EXCLUDED.ended_at,
            description = EXCLUDED.description,
            is_deleted = EXCLUDED.is_deleted,
            source_updated_at = EXCLUDED.source_updated_at,
            ingested_at = now()
        RETURNING activity_id
        """,
        (a.source, a.source_id, a.started_at, a.ended_at, a.description, a.is_deleted, a.source_updated_at),
    ).fetchone()[0]
    conn.execute("DELETE FROM core.activity_tag WHERE activity_id = %s", (activity_id,))
    if a.tags:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO core.activity_tag (activity_id, tag, position) VALUES (%s, %s, %s)",
                [(activity_id, tag, position) for position, tag in enumerate(a.tags, start=1)],
            )


def _read_cursor(conn: psycopg.Connection, source: str) -> str | None:
    row = conn.execute("SELECT cursor FROM ops.sync_state WHERE source = %s", (source,)).fetchone()
    return row[0] if row else None


def _save_cursor(conn: psycopg.Connection, source: str, cursor: str) -> None:
    conn.execute(
        """
        INSERT INTO ops.sync_state (source, cursor, last_success_at, last_error)
        VALUES (%s, %s, now(), NULL)
        ON CONFLICT (source) DO UPDATE
            SET cursor = EXCLUDED.cursor, last_success_at = now(), last_error = NULL
        """,
        (source, cursor),
    )


def _record_failure(conn: psycopg.Connection, source: str, error: str) -> None:
    conn.execute(
        """
        INSERT INTO ops.sync_state (source, last_error) VALUES (%s, %s)
        ON CONFLICT (source) DO UPDATE SET last_error = EXCLUDED.last_error
        """,
        (source, error),
    )


def _start_run(conn: psycopg.Connection, source: str, trigger: str) -> int:
    return conn.execute(
        "INSERT INTO ops.sync_run (source, trigger, status) VALUES (%s, %s, 'running') RETURNING run_id",
        (source, trigger),
    ).fetchone()[0]


def _finish_run(conn: psycopg.Connection, run_id: int, result: SyncResult) -> None:
    error = result.error
    if error is None and result.skipped_keys:
        error = "skipped: " + ", ".join(result.skipped_keys)
    conn.execute(
        """
        UPDATE ops.sync_run
        SET finished_at = now(), status = %s, n_records = %s, n_skipped = %s, error = %s
        WHERE run_id = %s
        """,
        (result.status, result.n_records, len(result.skipped_keys), error, run_id),
    )
