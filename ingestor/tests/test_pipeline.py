import dataclasses
from datetime import UTC, datetime

import psycopg
import pytest

from lifelog import pipeline
from lifelog.pipeline import SYNC_LOCK_KEY, run_sync
from lifelog.sources.timetagger import TimeTaggerError, TimeTaggerSource

T0 = 1_790_000_000


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.since_seen = []

    def get_updates(self, since):
        self.since_seen.append(since)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def rec(key="k1", t1=T0, t2=T0 + 3600, ds="#study #math", st=100.5):
    return {"key": key, "t1": t1, "t2": t2, "mt": t2, "ds": ds, "st": st}


def updates(*records, server_time=100.5):
    return {"server_time": server_time, "reset": 0, "records": list(records), "settings": []}


def sync(conn, client):
    return run_sync(conn, TimeTaggerSource(client), trigger="test", wait_for_lock=True)


def activities(conn):
    return conn.execute(
        "SELECT source_id, started_at, ended_at, is_deleted FROM core.activity ORDER BY source_id"
    ).fetchall()


def tags(conn, source_id="k1"):
    return conn.execute(
        """
        SELECT t.tag, t.position FROM core.activity_tag t
        JOIN core.activity a USING (activity_id)
        WHERE a.source_id = %s ORDER BY t.position
        """,
        (source_id,),
    ).fetchall()


def cursor(conn):
    row = conn.execute("SELECT cursor FROM ops.sync_state WHERE source = 'timetagger'").fetchone()
    return row[0] if row else None


def last_run(conn):
    return conn.execute(
        "SELECT trigger, status, n_records, n_skipped, error FROM ops.sync_run ORDER BY run_id DESC LIMIT 1"
    ).fetchone()


def test_first_sync_loads_raw_and_core(conn):
    result = sync(conn, FakeClient(updates(rec())))

    assert (result.status, result.n_records) == ("success", 1)
    assert activities(conn) == [
        ("k1", datetime.fromtimestamp(T0, UTC), datetime.fromtimestamp(T0 + 3600, UTC), False)
    ]
    assert tags(conn) == [("study", 1), ("math", 2)]
    assert cursor(conn) == "100.5"
    assert last_run(conn) == ("test", "success", 1, 0, None)


def test_next_sync_starts_from_saved_cursor(conn):
    client = FakeClient(updates(rec()), updates(server_time=150.0))
    sync(conn, client)
    sync(conn, client)
    assert client.since_seen == [0.0, 100.5]
    assert cursor(conn) == "150.0"


def test_repeated_records_are_idempotent(conn):
    sync(conn, FakeClient(updates(rec())))
    result = sync(conn, FakeClient(updates(rec())))
    assert result.n_records == 0
    assert len(activities(conn)) == 1
    assert tags(conn) == [("study", 1), ("math", 2)]


def test_updated_record_replaces_tags(conn):
    sync(conn, FakeClient(updates(rec())))
    sync(conn, FakeClient(updates(rec(ds="#gym", st=200.0), server_time=200.0)))
    assert tags(conn) == [("gym", 1)]


def test_hidden_record_is_soft_deleted(conn):
    sync(conn, FakeClient(updates(rec())))
    sync(conn, FakeClient(updates(rec(ds="HIDDEN #study #math", st=200.0), server_time=200.0)))
    assert activities(conn)[0][3] is True


def test_running_record_has_null_end(conn):
    sync(conn, FakeClient(updates(rec(t2=T0))))
    assert activities(conn)[0][2] is None


def test_bad_record_is_skipped_and_batch_continues(conn):
    result = sync(conn, FakeClient(updates(rec(key="bad", t2=T0 - 60), rec(key="good"))))

    assert result.status == "success"
    assert result.skipped_keys == ["bad"]
    assert [a[0] for a in activities(conn)] == ["good"]
    assert last_run(conn) == ("test", "success", 1, 1, "skipped: bad")


def test_failure_mid_transaction_commits_nothing(conn, monkeypatch):
    def boom(*args):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(pipeline, "_save_cursor", boom)
    result = sync(conn, FakeClient(updates(rec())))

    assert result.status == "failed"
    assert "disk on fire" in result.error
    assert conn.execute("SELECT count(*) FROM raw.timetagger_record").fetchone()[0] == 0
    assert activities(conn) == []
    assert cursor(conn) is None
    assert conn.execute("SELECT last_error FROM ops.sync_state").fetchone()[0].endswith("disk on fire")
    assert last_run(conn)[1] == "failed"


def test_source_error_marks_run_failed(conn):
    result = sync(conn, FakeClient(TimeTaggerError("unreachable")))
    assert result.status == "failed"
    assert last_run(conn)[1:3] == ("failed", 0)


def test_timer_skips_when_another_sync_holds_the_lock(conn, pg_url):
    with psycopg.connect(pg_url, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (SYNC_LOCK_KEY,))
        result = run_sync(conn, TimeTaggerSource(FakeClient()), trigger="timer", wait_for_lock=False)
    assert result.status == "skipped"
    assert conn.execute("SELECT count(*) FROM ops.sync_run").fetchone()[0] == 0


def test_lock_is_released_after_sync(conn, pg_url):
    sync(conn, FakeClient(updates()))
    with psycopg.connect(pg_url, autocommit=True) as other:
        assert other.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,)).fetchone()[0] is True


class ScriptedSource(TimeTaggerSource):
    """TimeTaggerSource whose to_activity can be overridden per key."""

    def __init__(self, client, overrides):
        super().__init__(client)
        self._overrides = overrides

    def to_activity(self, record):
        activity = super().to_activity(record)
        override = self._overrides.get(record["key"])
        return override(activity) if override else activity


def _connection_lost(activity):
    raise psycopg.OperationalError("connection lost")


def test_db_error_inside_a_record_aborts_the_run(conn):
    source = ScriptedSource(FakeClient(updates(rec("k1"), rec("k2"))), {"k2": _connection_lost})
    result = run_sync(conn, source, trigger="test", wait_for_lock=True)

    assert result.status == "failed"
    assert activities(conn) == []
    assert conn.execute("SELECT count(*) FROM raw.timetagger_record").fetchone()[0] == 0
    assert cursor(conn) is None


def test_savepoint_discards_partial_record(conn):
    def duplicate_tags(activity):
        return dataclasses.replace(activity, tags=("a", "a"))

    source = ScriptedSource(FakeClient(updates(rec("good"), rec("dup"))), {"dup": duplicate_tags})
    result = run_sync(conn, source, trigger="test", wait_for_lock=True)

    assert result.status == "success"
    assert result.skipped_keys == ["dup"]
    assert [a[0] for a in activities(conn)] == ["good"]


def test_removing_all_tags_clears_them(conn):
    sync(conn, FakeClient(updates(rec())))
    sync(conn, FakeClient(updates(rec(ds="no tags", st=200.0), server_time=200.0)))
    assert tags(conn) == []


def test_undeleting_a_record(conn):
    sync(conn, FakeClient(updates(rec(ds="HIDDEN #study"))))
    sync(conn, FakeClient(updates(rec(ds="#study", st=200.0), server_time=200.0)))
    assert activities(conn)[0][3] is False


def test_lock_released_after_failed_run(conn, pg_url):
    sync(conn, FakeClient(TimeTaggerError("unreachable")))
    with psycopg.connect(pg_url, autocommit=True) as other:
        assert other.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,)).fetchone()[0] is True


def test_success_after_failure_clears_last_error(conn):
    sync(conn, FakeClient(TimeTaggerError("unreachable")))
    assert "unreachable" in last_run(conn)[4]

    sync(conn, FakeClient(updates(rec())))
    assert conn.execute("SELECT last_error FROM ops.sync_state").fetchone()[0] is None
    assert cursor(conn) == "100.5"


def test_reset_overwrites_raw_even_if_not_newer(conn):
    sync(conn, FakeClient(updates(rec(ds="#gym", st=200.0), server_time=200.0)))
    reset = {"server_time": 210.0, "reset": 1, "records": [rec(ds="#study", st=150.0)], "settings": []}
    sync(conn, FakeClient(reset))
    assert tags(conn) == [("study", 1)]


def test_close_stale_runs(conn):
    conn.execute("INSERT INTO ops.sync_run (source, trigger, status) VALUES ('timetagger', 'timer', 'running')")
    conn.execute(
        "INSERT INTO ops.sync_run (source, trigger, status, finished_at) "
        "VALUES ('timetagger', 'timer', 'success', now())"
    )

    assert pipeline.close_stale_runs(conn) == 1
    rows = conn.execute("SELECT status, error FROM ops.sync_run ORDER BY run_id").fetchall()
    assert rows == [("failed", "interrupted (process stopped mid-run)"), ("success", None)]


def test_bookkeeping_failure_does_not_mask_result(conn, monkeypatch):
    def gone(*args):
        raise psycopg.OperationalError("gone")

    monkeypatch.setattr(pipeline, "_finish_run", gone)
    result = sync(conn, FakeClient(updates(rec())))
    assert result.status == "success"
