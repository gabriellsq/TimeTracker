"""Weekly study goals: subjects, validation, load and save.

HTTP wiring lives in app.py and the HTML in goals_page.py.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import psycopg

MAX_HOURS = Decimal("40")
STEP = Decimal("0.5")


class GoalError(ValueError):
    """Invalid goal input. The message is safe to show to the user."""


@dataclass(frozen=True)
class Subject:
    subject: str
    label: str


def current_week_start(conn: psycopg.Connection) -> date:
    return conn.execute("SELECT week_start FROM mart.v_current_week").fetchone()[0]


def list_subjects(conn: psycopg.Connection) -> list[Subject]:
    rows = conn.execute("SELECT subject, label FROM core.subject ORDER BY sort_order, subject").fetchall()
    return [Subject(*row) for row in rows]


def load_goals(conn: psycopg.Connection, week_start: date) -> dict[str, Decimal]:
    rows = conn.execute(
        "SELECT subject, target_hours FROM core.goal WHERE week_start = %s", (week_start,)
    ).fetchall()
    return dict(rows)


def study_hours(conn: psycopg.Connection, week_start: date) -> Decimal:
    """Study hours done in the given local week, with the same rules as the mart views.
    Inner join on purpose: least()/greatest() ignore NULLs, so a LEFT JOIN would turn 'no activity' into a full week.
    Meant for past weeks: unlike the views it does not clamp to now(), so for the current week it would count future-dated entries."""
    return conn.execute(
        """
        WITH w AS (
            SELECT %(w)s::timestamp AT TIME ZONE 'America/Vancouver' AS t0,
                   (%(w)s::timestamp + interval '7 days') AT TIME ZONE 'America/Vancouver' AS t1
        )
        SELECT round(coalesce(sum(extract(epoch FROM least(a.ended_at, w.t1) - greatest(a.started_at, w.t0))), 0)
                     / 3600, 1)
        FROM w
        JOIN mart.v_study_activity a ON a.started_at < w.t1 AND a.ended_at > w.t0
        """,
        {"w": week_start},
    ).fetchone()[0]


def parse_targets(raw: dict[str, str], subjects: list[Subject]) -> dict[str, Decimal]:
    """Turn form values into hours per subject, or raise GoalError."""
    known = {s.subject for s in subjects}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise GoalError(f"Unknown subject: {', '.join(unknown)}")
    targets = {}
    for subject, text in raw.items():
        try:
            value = Decimal(text.strip())
        except (InvalidOperation, AttributeError):
            raise GoalError(f"{subject}: not a number") from None
        if not value.is_finite() or value < 0 or value > MAX_HOURS:
            raise GoalError(f"{subject}: must be between 0 and 40 hours")
        if value % STEP != 0:
            raise GoalError(f"{subject}: use steps of 0.5 hours")
        targets[subject] = value
    missing = [s.label for s in subjects if s.subject not in raw]
    if missing:
        raise GoalError(f"Missing subject: {', '.join(missing)}")
    return targets


def save_goals(conn: psycopg.Connection, week_start: date, targets: dict[str, Decimal]) -> None:
    """Upsert this or a future week's goals in one transaction."""
    if week_start.isoweekday() != 1:
        raise GoalError("The week must start on a Monday")
    if week_start < current_week_start(conn):
        raise GoalError("Past weeks cannot be changed: a new week has started. Review the goals and save again.")
    with conn.transaction():
        for subject, hours in targets.items():
            conn.execute(
                """
                INSERT INTO core.goal (week_start, subject, target_hours) VALUES (%s, %s, %s)
                ON CONFLICT (week_start, subject)
                    DO UPDATE SET target_hours = EXCLUDED.target_hours, updated_at = now()
                """,
                (week_start, subject, hours),
            )
