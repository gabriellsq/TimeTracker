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


def ago(text):
    return f"now() - interval '{text}'"


def flagged(conn):
    return conn.execute("SELECT display FROM mart.v_long_timers ORDER BY started_at").fetchall()


def test_running_timer_over_the_limit_is_flagged(conn):
    add_activity(conn, "r", ago("7 hours 12 minutes"), "NULL", ["study", "personalproject"])
    [(display,)] = flagged(conn)
    assert display == "⚠️ Running 7 h 12 m · #study #personalproject — forgot to stop?"


def test_running_timer_under_the_limit_is_not_flagged(conn):
    add_activity(conn, "r", ago("2 hours"), "NULL", ["study"])
    assert flagged(conn) == []


def test_recent_long_entry_is_flagged(conn):
    add_activity(conn, "f", ago("2 days"), ago("2 days") + " + interval '23 hours 12 minutes'", ["study"])
    [(display,)] = flagged(conn)
    assert display.startswith("⚠️ 23.2 h on ")
    assert display.endswith(" · #study — fix in TimeTagger or tag #long")


def test_entry_just_under_the_limit_is_not_flagged(conn):
    add_activity(conn, "f", ago("1 day"), ago("1 day") + " + interval '5 hours 59 minutes'", ["study"])
    assert flagged(conn) == []


def test_old_long_entry_ages_out(conn):
    add_activity(conn, "f", ago("9 days"), ago("9 days") + " + interval '8 hours'", ["study"])
    assert flagged(conn) == []


def test_long_tag_opts_out(conn):
    add_activity(conn, "f", ago("1 day"), ago("1 day") + " + interval '8 hours'", ["study", "long"])
    add_activity(conn, "r", ago("9 hours"), "NULL", ["long"])
    assert flagged(conn) == []


def test_deleted_entries_are_ignored(conn):
    add_activity(conn, "f", ago("1 day"), ago("1 day") + " + interval '8 hours'", ["study"], deleted=True)
    assert flagged(conn) == []


def test_untagged_entry_says_no_tags(conn):
    add_activity(conn, "r", ago("6 hours 30 minutes"), "NULL", [])
    [(display,)] = flagged(conn)
    assert display == "⚠️ Running 6 h 30 m · (no tags) — forgot to stop?"


def test_grafana_role_can_read_long_timers(conn):
    conn.execute("SET ROLE grafana_ro")
    try:
        conn.execute("SELECT * FROM mart.v_long_timers").fetchall()
    finally:
        conn.execute("RESET ROLE")
