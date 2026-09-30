from datetime import timedelta
from decimal import Decimal

import pytest

from lifelog import goals
from lifelog.goals import GoalError, Subject

SUBJECTS = [Subject("ds", "DS and Algorithms"), Subject("systemanalysis", "System Analysis")]


def test_parse_targets_accepts_half_hours():
    assert goals.parse_targets({"ds": "8", "systemanalysis": "4.5"}, SUBJECTS) == {
        "ds": Decimal("8"),
        "systemanalysis": Decimal("4.5"),
    }


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"chess": "1"}, "Unknown subject"),
        ({"ds": "abc"}, "not a number"),
        ({"ds": "nan"}, "between 0 and 40"),
        ({"ds": "-1"}, "between 0 and 40"),
        ({"ds": "40.5"}, "between 0 and 40"),
        ({"ds": "8.3"}, "steps of 0.5"),
        ({"ds": "8"}, "Missing subject"),
    ],
)
def test_parse_targets_rejects_bad_input(raw, message):
    with pytest.raises(GoalError, match=message):
        goals.parse_targets(raw, SUBJECTS)


def test_current_week_start_is_a_monday(conn):
    assert goals.current_week_start(conn).isoweekday() == 1


def test_list_subjects_in_display_order(conn):
    assert goals.list_subjects(conn) == SUBJECTS


def test_save_and_load_round_trip(conn):
    week = goals.current_week_start(conn)
    goals.save_goals(conn, week, {"ds": Decimal("8"), "systemanalysis": Decimal("4")})
    assert goals.load_goals(conn, week) == {"ds": Decimal("8.0"), "systemanalysis": Decimal("4.0")}


def test_save_replaces_existing_values(conn):
    week = goals.current_week_start(conn)
    goals.save_goals(conn, week, {"ds": Decimal("8")})
    goals.save_goals(conn, week, {"ds": Decimal("6.5")})
    assert goals.load_goals(conn, week) == {"ds": Decimal("6.5")}


def test_save_rejects_non_monday(conn):
    week = goals.current_week_start(conn)
    with pytest.raises(GoalError, match="Monday"):
        goals.save_goals(conn, week + timedelta(days=1), {"ds": Decimal("1")})


def test_save_rejects_past_weeks(conn):
    last_week = goals.current_week_start(conn) - timedelta(days=7)
    with pytest.raises(GoalError, match="Past weeks"):
        goals.save_goals(conn, last_week, {"ds": Decimal("1")})


def test_study_hours_of_a_given_week(conn):
    last_week = goals.current_week_start(conn) - timedelta(days=7)
    activity_id = conn.execute(
        """
        INSERT INTO core.activity (source, source_id, started_at, ended_at)
        VALUES ('test', 'a',
                (%(w)s::timestamp + interval '1 hour') AT TIME ZONE 'America/Vancouver',
                (%(w)s::timestamp + interval '3 hours') AT TIME ZONE 'America/Vancouver')
        RETURNING activity_id
        """,
        {"w": last_week},
    ).fetchone()[0]
    conn.execute("INSERT INTO core.activity_tag (activity_id, tag, position) VALUES (%s, 'study', 1)", (activity_id,))
    assert goals.study_hours(conn, last_week) == Decimal("2.0")
    assert goals.study_hours(conn, last_week + timedelta(days=7)) == Decimal("0.0")
