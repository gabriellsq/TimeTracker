WEEK_START = "(date_trunc('week', now() AT TIME ZONE 'America/Vancouver') AT TIME ZONE 'America/Vancouver')"


def add_activity(conn, source_id, start_sql, end_sql, tags, deleted=False):
    activity_id = conn.execute(
        f"""
        INSERT INTO core.activity (source, source_id, started_at, ended_at, is_deleted)
        VALUES ('test', %s, {start_sql}, {end_sql}, %s)
        RETURNING activity_id
        """,
        (source_id, deleted),
    ).fetchone()[0]
    for position, tag in enumerate(tags, start=1):
        conn.execute(
            "INSERT INTO core.activity_tag (activity_id, tag, position) VALUES (%s, %s, %s)",
            (activity_id, tag, position),
        )


def week_hours(conn):
    return dict(conn.execute("SELECT tag, hours FROM mart.v_week_tag_hours").fetchall())


def test_counts_this_weeks_hours_per_tag(conn):
    add_activity(conn, "a", f"{WEEK_START} + interval '1 hour'", f"{WEEK_START} + interval '3 hours'", ["study", "math"])
    hours = week_hours(conn)
    assert float(hours["study"]) == 2.0
    assert float(hours["math"]) == 2.0


def test_ignores_last_week_and_deleted(conn):
    add_activity(conn, "old", f"{WEEK_START} - interval '2 hours'", f"{WEEK_START} - interval '1 hour'", ["study"])
    add_activity(conn, "del", f"{WEEK_START} + interval '1 hour'", f"{WEEK_START} + interval '2 hours'", ["gym"], deleted=True)
    assert week_hours(conn) == {}


def test_running_activity_counts_until_now(conn):
    add_activity(conn, "run", "now() - interval '30 minutes'", "NULL", ["study"])
    assert float(week_hours(conn)["study"]) > 0


def test_sync_status_shows_latest_run_per_source(conn):
    conn.execute(
        """
        INSERT INTO ops.sync_run (source, trigger, started_at, finished_at, status, error) VALUES
            ('timetagger', 'timer', now() - interval '10 minutes', now() - interval '10 minutes', 'failed', 'boom'),
            ('timetagger', 'timer', now() - interval '1 minute',  now() - interval '1 minute',  'success', NULL)
        """
    )
    rows = conn.execute("SELECT source, status, minutes_ago FROM mart.v_sync_status").fetchall()
    assert rows == [("timetagger", "success", 1)]


def test_grafana_role_cannot_read_core_or_raw(conn):
    # Checked as the superuser: grafana_ro cannot even resolve names in core/raw.
    for table in ["core.activity", "core.activity_tag", "raw.timetagger_record"]:
        assert conn.execute("SELECT has_table_privilege('grafana_ro', %s, 'SELECT')", (table,)).fetchone()[0] is False


def test_grafana_role_can_read_mart_views(conn):
    conn.execute("SET ROLE grafana_ro")
    try:
        conn.execute("SELECT * FROM mart.v_week_tag_hours").fetchall()
        conn.execute("SELECT * FROM mart.v_sync_status").fetchall()
    finally:
        conn.execute("RESET ROLE")


def test_untagged_activity_is_counted(conn):
    add_activity(conn, "u", f"{WEEK_START} + interval '1 hour'", f"{WEEK_START} + interval '2 hours'", [])
    assert float(week_hours(conn)["(untagged)"]) == 1.0
