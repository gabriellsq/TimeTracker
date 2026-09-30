import pytest

T0 = "(SELECT t0 FROM mart.v_current_week)"
WEEK = "(SELECT week_start FROM mart.v_current_week)"


def at(hours):
    """SQL for a moment `hours` after the start of the current week (negative = last week)."""
    return f"{T0} + interval '{hours} hours'"


@pytest.fixture(autouse=True)
def _enough_of_the_week_has_passed(conn):
    if conn.execute(f"SELECT now() < {at(5)}").fetchone()[0]:
        pytest.skip("study view tests need the first 5 hours of the current week to have passed")


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


def set_goal(conn, subject, hours):
    conn.execute(f"INSERT INTO core.goal (week_start, subject, target_hours) VALUES ({WEEK}, %s, %s)", (subject, hours))


def week(conn):
    return conn.execute("SELECT hours, sessions, goal_hours, status, summary FROM mart.v_study_week").fetchone()


def test_current_week_starts_on_monday(conn):
    week_start, t0, t1 = conn.execute("SELECT week_start, t0, t1 FROM mart.v_current_week").fetchone()
    assert week_start.isoweekday() == 1
    assert t0 < t1


def test_study_counted_once_with_several_tags(conn):
    add_activity(conn, "a", at(1), at(3), ["study", "ds/algorithms"])
    hours, sessions, *_ = week(conn)
    assert (float(hours), sessions) == (2.0, 1)


def test_subject_tag_without_study_counts(conn):
    add_activity(conn, "a", at(1), at(2.5), ["systemanalysis"])
    assert float(week(conn)[0]) == 1.5


def test_prefix_matches_only_below_ds(conn):
    add_activity(conn, "x", at(1), at(2), ["dsx"])
    add_activity(conn, "d", at(3), at(4), ["ds"])
    hours, sessions, *_ = week(conn)
    assert (float(hours), sessions) == (1.0, 1)


def test_non_study_and_deleted_are_excluded(conn):
    add_activity(conn, "g", at(1), at(2), ["gym"])
    add_activity(conn, "d", at(3), at(4), ["study"], deleted=True)
    hours, sessions, *_ = week(conn)
    assert (float(hours), sessions) == (0.0, 0)


def test_activity_crossing_week_start_counts_only_in_week_part(conn):
    add_activity(conn, "a", at(-1), at(1), ["study"])
    assert float(week(conn)[0]) == 1.0


def test_week_without_goal(conn):
    add_activity(conn, "a", at(1), at(2), ["study"])
    _, _, goal, status, summary = week(conn)
    assert goal is None
    assert status == "no goal"
    assert summary.endswith("1 session · no goal set")
    paces = conn.execute("SELECT count(pace_hours) FROM mart.v_study_week_cumulative").fetchone()[0]
    assert paces == 0


def test_goal_is_sum_of_subjects_and_pace_reaches_it(conn):
    set_goal(conn, "ds", 8)
    set_goal(conn, "systemanalysis", 4)
    assert float(week(conn)[2]) == 12.0
    first, last = conn.execute(
        """
        SELECT (SELECT pace_hours FROM mart.v_study_week_cumulative ORDER BY time LIMIT 1),
               (SELECT pace_hours FROM mart.v_study_week_cumulative ORDER BY time DESC LIMIT 1)
        """
    ).fetchone()
    assert (float(first), float(last)) == (0.0, 12.0)


def test_status_done_and_behind(conn):
    set_goal(conn, "ds", 1)
    add_activity(conn, "a", at(1), at(3), ["ds/graphs"])
    assert week(conn)[3] == "done"
    conn.execute("UPDATE core.goal SET target_hours = 40")
    conn.execute("DELETE FROM core.activity")
    assert week(conn)[3] == "behind"


def test_summary_text(conn):
    set_goal(conn, "ds", 8)
    set_goal(conn, "systemanalysis", 4)
    add_activity(conn, "a", at(1), at(2), ["study"])
    summary = week(conn)[4]
    assert " – " in summary
    assert "1 session · goal 12 h · " in summary


def test_cumulative_actual_reaches_total_until_now(conn):
    add_activity(conn, "a", at(1), at(2), ["study"])
    latest = conn.execute(
        """
        SELECT actual_hours FROM mart.v_study_week_cumulative
        WHERE actual_hours IS NOT NULL ORDER BY time DESC LIMIT 1
        """
    ).fetchone()[0]
    assert float(latest) == 1.0
    future = conn.execute(
        "SELECT count(actual_hours) FROM mart.v_study_week_cumulative WHERE time > now()"
    ).fetchone()[0]
    assert future == 0


def test_subject_rows(conn):
    set_goal(conn, "ds", 8)
    add_activity(conn, "a", at(1), at(3), ["study", "ds/algorithms"])
    rows = conn.execute("SELECT subject, display FROM mart.v_study_subject_week ORDER BY sort_order").fetchall()
    assert rows == [
        ("ds", "DS and Algorithms · 2 / 8 h"),
        ("systemanalysis", "System Analysis · 0 / – h"),
    ]


def test_grafana_role_can_read_study_views(conn):
    views = [
        "v_current_week",
        "v_study_activity",
        "v_study_week_activity",
        "v_study_week",
        "v_study_week_cumulative",
        "v_study_subject_week",
    ]
    conn.execute("SET ROLE grafana_ro")
    try:
        for view in views:
            conn.execute(f"SELECT * FROM mart.{view}").fetchall()
    finally:
        conn.execute("RESET ROLE")


def test_running_activity_counts_until_now(conn):
    add_activity(conn, "r", "now() - interval '30 minutes'", "NULL", ["study"])
    assert float(week(conn)[0]) == 0.5


def test_future_part_is_not_counted_yet(conn):
    add_activity(conn, "f", "now() - interval '1 hour'", "now() + interval '5 hours'", ["study"])
    hours, sessions, *_ = week(conn)
    assert (float(hours), sessions) == (1.0, 1)
    latest = conn.execute(
        "SELECT actual_hours FROM mart.v_study_week_cumulative WHERE actual_hours IS NOT NULL ORDER BY time DESC LIMIT 1"
    ).fetchone()[0]
    assert round(float(latest), 1) == 1.0


def test_future_activity_is_not_counted(conn):
    add_activity(conn, "f", "now() + interval '1 hour'", "now() + interval '2 hours'", ["study"])
    hours, sessions, *_ = week(conn)
    assert (float(hours), sessions) == (0.0, 0)


def test_zero_duration_is_not_a_session(conn):
    add_activity(conn, "z", at(1), at(1), ["study"])
    assert week(conn)[1] == 0


def test_goal_of_zero_means_no_goal(conn):
    set_goal(conn, "ds", 0)
    _, _, goal, status, summary = week(conn)
    assert (goal, status) == (None, "no goal")
    assert summary.endswith("no goal set")


def test_done_uses_the_rounded_total(conn):
    set_goal(conn, "ds", 2)
    add_activity(conn, "a", at(1), f"{T0} + interval '2 hours 59 minutes 57 seconds'", ["ds"])
    hours, _, _, status, _ = week(conn)
    assert (float(hours), status) == (2.0, "done")
