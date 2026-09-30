# Study Card and Weekly Goals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Garmin-style Study card in Grafana (weekly total + cumulative line vs goal pace + subject rows) and a password-protected `/goals` page with sliders to set each week's subject goals.

**Architecture:** Migration `005` adds `core.subject` (tag rules) and `core.goal` (one row per week and subject). New mart views compute the current week's study hours, pace and subject progress; Grafana reads only those. The ingestor gains `goals.py` (validation, load/save), `goals_page.py` (HTML) and two routes in `app.py`. Caddy puts basic auth in front of the whole `sync.` host.

**Tech Stack:** PostgreSQL 17 views, Python 3.12 (FastAPI, psycopg 3, stdlib `html`/`urllib`), Caddy 2 `basic_auth`, Grafana 13 provisioning JSON.

**Spec:** `docs/superpowers/specs/2026-09-30-study-card-and-goals-design.md`

---

## Environment notes (this repo, Windows dev machine)

- `uv` is not on PATH: run `python -m uv ...`. Tests: `cd ingestor && python -m uv run pytest -q` (Docker must be running; tests use testcontainers `postgres:17`).
- Never delete `ingestor/.venv`, uv caches or `uv.lock`. Edit files with an editor/Write tool (ad-hoc Python edit scripts produced CRLF and encoding bugs here). All files are LF (`.gitattributes`).
- In Git Bash, prefix `docker run ... -v host:container` commands with `MSYS_NO_PATHCONV=1`.
- Commit messages have **no** `Co-Authored-By` or other attribution trailer.
- Test fixtures (`ingestor/tests/conftest.py`): `pg_url` (session container URL), `empty_db`, `conn` (fully set up DB incl. all migrations and mart views; autocommit connection as superuser; role `grafana_ro` exists). `DB_DIR` = repo `db/`.

## File structure

```
db/migrations/005_study_goals.sql            core.subject (+ seed), core.goal
db/views/030_v_current_week.sql              bounds of the current local week
db/views/031_v_study_activity.sql            one row per study activity, with its subjects
db/views/032_v_study_week_activity.sql       study activities overlapping this week + in-week hours
db/views/033_v_study_week.sql                weekly summary (hours, sessions, goal, pace, status, text)
db/views/034_v_study_week_cumulative.sql     hourly points for the line chart
db/views/035_v_study_subject_week.sql        per-subject hours vs goal
ingestor/src/lifelog/goals.py                subjects, validation, load/save, study hours of a week
ingestor/src/lifelog/goals_page.py           HTML rendering of the goals page
ingestor/src/lifelog/app.py                  + GET/POST /goals, same-origin check
ingestor/tests/test_study_views.py
ingestor/tests/test_goals.py
ingestor/tests/test_goals_http.py
caddy/Caddyfile, compose.yaml, .env.example  basic auth on sync.<domain>
grafana/dashboards/this-week.json            Study section + "Set goals" link
docs/setup.md                                new .env values, goals page
```

---

### Task 1: Migration for subjects and goals

**Files:**
- Create: `db/migrations/005_study_goals.sql`
- Test: `ingestor/tests/test_db.py` (append)

- [ ] **Step 1: Append failing tests to `ingestor/tests/test_db.py`**

```python
def test_subjects_are_seeded(conn):
    rows = conn.execute("SELECT subject, label, tags, tag_prefix FROM core.subject ORDER BY sort_order").fetchall()
    assert rows == [
        ("ds", "DS and Algorithms", ["ds"], "ds/"),
        ("systemanalysis", "System Analysis", ["systemanalysis"], None),
    ]


def test_goal_week_must_start_on_monday(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO core.goal (week_start, subject, target_hours) VALUES ('2026-09-29', 'ds', 8)")


def test_goal_hours_are_bounded(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO core.goal (week_start, subject, target_hours) VALUES ('2026-09-28', 'ds', 41)")
```

If `test_db.py` does not already import `pytest` and `psycopg`, add `import psycopg` and `import pytest` at the top.

- [ ] **Step 2: Run to verify failure**

Run (in `ingestor/`): `python -m uv run pytest tests/test_db.py -v`
Expected: the 3 new tests FAIL (`relation "core.subject" does not exist`).

- [ ] **Step 3: Create `db/migrations/005_study_goals.sql`**

```sql
-- Subjects: which tags count toward which study subject.
-- An activity matches a subject if one of its tags is in `tags` or starts with `tag_prefix`.
CREATE TABLE core.subject (
    subject     text PRIMARY KEY,
    label       text NOT NULL,
    tags        text[] NOT NULL DEFAULT '{}',
    tag_prefix  text,
    sort_order  smallint NOT NULL DEFAULT 0
);

INSERT INTO core.subject (subject, label, tags, tag_prefix, sort_order) VALUES
    ('ds', 'DS and Algorithms', '{ds}', 'ds/', 1),
    ('systemanalysis', 'System Analysis', '{systemanalysis}', NULL, 2);

-- Weekly goals, set on the /goals page. One row per week and subject.
CREATE TABLE core.goal (
    week_start    date NOT NULL CHECK (extract(isodow FROM week_start) = 1),
    subject       text NOT NULL REFERENCES core.subject,
    target_hours  numeric(4, 1) NOT NULL CHECK (target_hours >= 0 AND target_hours <= 40),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (week_start, subject)
);
```

- [ ] **Step 4: Run tests**

Run: `python -m uv run pytest -q`
Expected: all pass (`test_migrate_applies_all_files_in_order` picks up `005` automatically).

- [ ] **Step 5: Commit**

```bash
git add db/migrations/005_study_goals.sql ingestor/tests/test_db.py
git commit -m "feat(db): add study subjects and weekly goals tables"
```

---

### Task 2: Study mart views

**Files:**
- Create: `db/views/030_v_current_week.sql`, `031_v_study_activity.sql`, `032_v_study_week_activity.sql`, `033_v_study_week.sql`, `034_v_study_week_cumulative.sql`, `035_v_study_subject_week.sql`
- Test: `ingestor/tests/test_study_views.py`

- [ ] **Step 1: Write the failing tests — `ingestor/tests/test_study_views.py`**

```python
import pytest

T0 = "(SELECT t0 FROM mart.v_current_week)"
WEEK = "(SELECT week_start FROM mart.v_current_week)"


def at(hours):
    """SQL for a moment `hours` after the start of the current week (negative = last week)."""
    return f"{T0} + interval '{hours} hours'"


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
    if conn.execute(f"SELECT now() < {at(3)}").fetchone()[0]:
        pytest.skip("needs at least 3 hours of the current week to have passed")
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m uv run pytest tests/test_study_views.py -v`
Expected: FAIL (`relation "mart.v_current_week" does not exist`).

- [ ] **Step 3: Create the views**

`db/views/030_v_current_week.sql`:
```sql
-- Bounds of the current local week: Monday 00:00 to next Monday 00:00 in America/Vancouver.
-- The timezone is hard-coded until M2's core.local_tz().
CREATE VIEW mart.v_current_week AS
SELECT local_monday::date AS week_start,
       local_monday AT TIME ZONE 'America/Vancouver' AS t0,
       (local_monday + interval '7 days') AT TIME ZONE 'America/Vancouver' AS t1
FROM (SELECT date_trunc('week', now() AT TIME ZONE 'America/Vancouver') AS local_monday) m;
```

`db/views/031_v_study_activity.sql`:
```sql
-- Activities that count as study: tagged #study or with a subject tag (see core.subject).
-- One row per activity, however many tags it has. Running activities end at now().
CREATE VIEW mart.v_study_activity AS
SELECT a.activity_id,
       a.started_at,
       coalesce(a.ended_at, now()) AS ended_at,
       coalesce(array_agg(DISTINCT s.subject) FILTER (WHERE s.subject IS NOT NULL), '{}') AS subjects
FROM core.activity a
JOIN core.activity_tag t USING (activity_id)
LEFT JOIN core.subject s
       ON t.tag = ANY (s.tags)
       OR (s.tag_prefix IS NOT NULL AND starts_with(t.tag, s.tag_prefix))
WHERE NOT a.is_deleted
GROUP BY a.activity_id, a.started_at, a.ended_at
HAVING bool_or(t.tag = 'study') OR count(s.subject) > 0;
```

`db/views/032_v_study_week_activity.sql`:
```sql
-- Study activities overlapping the current week, with the hours that fall inside it.
CREATE VIEW mart.v_study_week_activity AS
SELECT sa.activity_id,
       sa.started_at,
       sa.ended_at,
       sa.subjects,
       extract(epoch FROM least(sa.ended_at, w.t1) - greatest(sa.started_at, w.t0)) / 3600 AS hours
FROM mart.v_study_activity sa
CROSS JOIN mart.v_current_week w
WHERE sa.started_at < w.t1
  AND sa.ended_at > w.t0;
```

`db/views/033_v_study_week.sql`:
```sql
-- Current week study summary for the Study card.
-- goal_hours is the sum of this week's subject goals (NULL when none are set).
CREATE VIEW mart.v_study_week AS
SELECT s.*,
       s.week_label || ' · ' || s.sessions || CASE WHEN s.sessions = 1 THEN ' session' ELSE ' sessions' END
       || CASE WHEN s.goal_hours IS NULL THEN ' · no goal set'
               ELSE ' · goal ' || trim_scale(s.goal_hours) || ' h · ' || s.status END AS summary
FROM (
    SELECT w.week_start,
           w.week_start + 6 AS week_end,
           round(done.hours, 1) AS hours,
           done.sessions,
           goal.goal_hours,
           round(pace.expected, 1) AS expected_hours,
           CASE WHEN goal.goal_hours IS NULL THEN 'no goal'
                WHEN done.hours >= goal.goal_hours THEN 'done'
                WHEN done.hours >= pace.expected THEN 'ahead'
                ELSE 'behind' END AS status,
           to_char(w.week_start, 'Mon FMDD') || ' – ' || to_char(w.week_start + 6, 'Mon FMDD') AS week_label
    FROM mart.v_current_week w
    CROSS JOIN (
        SELECT coalesce(sum(hours), 0) AS hours, count(*) AS sessions FROM mart.v_study_week_activity
    ) done
    CROSS JOIN (
        SELECT sum(g.target_hours) AS goal_hours
        FROM core.goal g JOIN mart.v_current_week cw ON g.week_start = cw.week_start
    ) goal
    CROSS JOIN LATERAL (
        SELECT goal.goal_hours * extract(epoch FROM least(now(), w.t1) - w.t0)
               / extract(epoch FROM w.t1 - w.t0) AS expected
    ) pace
) s;
```

`db/views/034_v_study_week_cumulative.sql`:
```sql
-- Points for the Study line chart: every hour of the current week plus "now".
-- actual_hours: cumulative study hours up to that point (NULL in the future).
-- pace_hours: goal spread evenly over the week (NULL when no goal is set).
CREATE VIEW mart.v_study_week_cumulative AS
WITH goal AS (
    SELECT sum(g.target_hours) AS goal_hours
    FROM core.goal g JOIN mart.v_current_week w ON g.week_start = w.week_start
), points AS (
    SELECT generate_series(w.t0, w.t1, interval '1 hour') AS t FROM mart.v_current_week w
    UNION
    SELECT now()
)
SELECT p.t AS time,
       CASE WHEN p.t <= now() THEN (
           SELECT coalesce(sum(extract(epoch FROM least(a.ended_at, p.t) - greatest(a.started_at, w.t0))), 0) / 3600
           FROM mart.v_study_week_activity a
           WHERE a.started_at < p.t
       ) END AS actual_hours,
       goal.goal_hours * extract(epoch FROM p.t - w.t0) / extract(epoch FROM w.t1 - w.t0) AS pace_hours
FROM points p
CROSS JOIN mart.v_current_week w
CROSS JOIN goal;
```

`db/views/035_v_study_subject_week.sql`:
```sql
-- Hours and goal per subject for the current week. An activity with two subjects counts for both.
CREATE VIEW mart.v_study_subject_week AS
SELECT s.subject,
       s.label,
       s.sort_order,
       round(coalesce(h.hours, 0), 1) AS hours,
       g.target_hours AS goal_hours,
       s.label || ' · ' || trim_scale(round(coalesce(h.hours, 0), 1)) || ' / '
           || coalesce(trim_scale(g.target_hours)::text, '–') || ' h' AS display
FROM core.subject s
CROSS JOIN mart.v_current_week w
LEFT JOIN core.goal g ON g.week_start = w.week_start AND g.subject = s.subject
LEFT JOIN (
    SELECT subj AS subject, sum(a.hours) AS hours
    FROM mart.v_study_week_activity a
    CROSS JOIN LATERAL unnest(a.subjects) AS subj
    GROUP BY subj
) h ON h.subject = s.subject;
```

- [ ] **Step 4: Run tests**

Run: `python -m uv run pytest tests/test_study_views.py -v` then `python -m uv run pytest -q`
Expected: all pass (`test_cumulative_actual_reaches_total_until_now` may SKIP during the first 3 hours of a Monday in Vancouver — that is expected). If a view test fails, fix the SQL, not the test, and describe the fix.

- [ ] **Step 5: Commit**

```bash
git add db/views/030_v_current_week.sql db/views/031_v_study_activity.sql db/views/032_v_study_week_activity.sql db/views/033_v_study_week.sql db/views/034_v_study_week_cumulative.sql db/views/035_v_study_subject_week.sql ingestor/tests/test_study_views.py
git commit -m "feat(db): add study week views for the Study card"
```

---

### Task 3: Goals logic

**Files:**
- Create: `ingestor/src/lifelog/goals.py`
- Test: `ingestor/tests/test_goals.py`

- [ ] **Step 1: Write the failing tests — `ingestor/tests/test_goals.py`**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m uv run pytest tests/test_goals.py -v`
Expected: FAIL (`ImportError: cannot import name 'goals' from 'lifelog'`).

- [ ] **Step 3: Implement `ingestor/src/lifelog/goals.py`**

```python
"""Weekly study goals: subjects, validation, load and save.

HTTP wiring lives in app.py and the HTML in goals_page.py.
"""

from dataclasses import dataclass
from datetime import date
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
    """Study hours done in the given local week, with the same rules as the mart views."""
    return conn.execute(
        """
        WITH w AS (
            SELECT %(w)s::timestamp AT TIME ZONE 'America/Vancouver' AS t0,
                   (%(w)s::timestamp + interval '7 days') AT TIME ZONE 'America/Vancouver' AS t1
        )
        SELECT round(coalesce(sum(extract(epoch FROM least(a.ended_at, w.t1) - greatest(a.started_at, w.t0))), 0)
                     / 3600, 1)
        FROM w
        LEFT JOIN mart.v_study_activity a ON a.started_at < w.t1 AND a.ended_at > w.t0
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
    return targets


def save_goals(conn: psycopg.Connection, week_start: date, targets: dict[str, Decimal]) -> None:
    """Upsert this or a future week's goals in one transaction."""
    if week_start.isoweekday() != 1:
        raise GoalError("The week must start on a Monday")
    if week_start < current_week_start(conn):
        raise GoalError("Past weeks cannot be changed")
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
```

- [ ] **Step 4: Run tests**

Run: `python -m uv run pytest tests/test_goals.py -v` then `python -m uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ingestor/src/lifelog/goals.py ingestor/tests/test_goals.py
git commit -m "feat(ingestor): add weekly goals validation and storage"
```

---

### Task 4: Goals page and routes

**Files:**
- Create: `ingestor/src/lifelog/goals_page.py`
- Modify: `ingestor/src/lifelog/app.py`
- Test: `ingestor/tests/test_goals_http.py`

- [ ] **Step 1: Write the failing tests — `ingestor/tests/test_goals_http.py`**

```python
from datetime import timedelta

from fastapi.testclient import TestClient

from lifelog import app as app_module
from lifelog import goals
from lifelog.config import Settings

DASHBOARD = "https://dash.lifelog.lan/d/this-week"
SAME_ORIGIN = {"Origin": "http://testserver"}


def client_for(pg_url):
    settings = Settings(
        database_url=pg_url,
        timetagger_api_url="http://tt/timetagger/api/v2",
        timetagger_token="tok",
        sync_interval_seconds=120,
        dashboard_url=DASHBOARD,
        db_dir="/unused",
    )
    # No `with` block: the lifespan does not run; the `conn` fixture has set up the database.
    return TestClient(app_module.create_app(settings, start_timer=False))


def saved(conn):
    return {k: float(v) for k, v in goals.load_goals(conn, goals.current_week_start(conn)).items()}


def test_page_suggests_last_weeks_goals(conn, pg_url):
    last_week = goals.current_week_start(conn) - timedelta(days=7)
    conn.execute(
        "INSERT INTO core.goal (week_start, subject, target_hours) VALUES (%s, 'ds', 8), (%s, 'systemanalysis', 4)",
        (last_week, last_week),
    )
    page = client_for(pg_url).get("/goals")
    assert page.status_code == 200
    assert "DS and Algorithms" in page.text and "System Analysis" in page.text
    assert 'name="goal_ds"' in page.text and 'value="8"' in page.text
    assert "Suggested from last week" in page.text
    assert "Last week: planned 12 h, did 0 h" in page.text


def test_page_shows_saved_goals(conn, pg_url):
    week = goals.current_week_start(conn)
    conn.execute("INSERT INTO core.goal (week_start, subject, target_hours) VALUES (%s, 'ds', 6.5)", (week,))
    page = client_for(pg_url).get("/goals")
    assert 'value="6.5"' in page.text
    assert "Saved" in page.text


def test_post_saves_goals_and_redirects(conn, pg_url):
    week = goals.current_week_start(conn)
    response = client_for(pg_url).post(
        "/goals",
        data={"week_start": week.isoformat(), "goal_ds": "8", "goal_systemanalysis": "4"},
        headers=SAME_ORIGIN,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == DASHBOARD
    assert saved(conn) == {"ds": 8.0, "systemanalysis": 4.0}


def test_post_rejects_bad_value(conn, pg_url):
    week = goals.current_week_start(conn)
    response = client_for(pg_url).post(
        "/goals", data={"week_start": week.isoformat(), "goal_ds": "8.3"}, headers=SAME_ORIGIN
    )
    assert response.status_code == 400
    assert "steps of 0.5" in response.text
    assert saved(conn) == {}


def test_post_rejects_invalid_week(conn, pg_url):
    response = client_for(pg_url).post("/goals", data={"week_start": "soon", "goal_ds": "1"}, headers=SAME_ORIGIN)
    assert response.status_code == 400


def test_post_rejects_foreign_origin(conn, pg_url):
    week = goals.current_week_start(conn)
    response = client_for(pg_url).post(
        "/goals",
        data={"week_start": week.isoformat(), "goal_ds": "8"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert saved(conn) == {}


def test_post_rejects_missing_origin(conn, pg_url):
    week = goals.current_week_start(conn)
    response = client_for(pg_url).post("/goals", data={"week_start": week.isoformat(), "goal_ds": "8"})
    assert response.status_code == 403


def test_page_escapes_labels(conn, pg_url):
    conn.execute("UPDATE core.subject SET label = '<b>DS</b>' WHERE subject = 'ds'")
    page = client_for(pg_url).get("/goals")
    assert "<b>DS</b>" not in page.text
    assert "&lt;b&gt;DS&lt;/b&gt;" in page.text
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m uv run pytest tests/test_goals_http.py -v`
Expected: FAIL (404 on `/goals`).

- [ ] **Step 3: Create `ingestor/src/lifelog/goals_page.py`**

```python
"""HTML for the weekly goals page: one self-contained page, no framework, works on a phone."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from html import escape

from lifelog.goals import MAX_HOURS, STEP, Subject


@dataclass(frozen=True)
class GoalsView:
    week_start: date
    subjects: list[Subject]
    values: dict[str, Decimal]  # what the sliders show, per subject
    saved: bool  # True: this week's saved goals. False: suggestion, not saved yet
    last_week_planned: Decimal | None
    last_week_done: Decimal
    dashboard_url: str
    error: str | None = None


def hours(value: Decimal) -> str:
    """8.0 -> '8', 4.5 -> '4.5'."""
    return f"{value.normalize():f}"


def week_label(start: date) -> str:
    end = start + timedelta(days=6)
    return f"{start:%b} {start.day} – {end:%b} {end.day}"


_STYLE = """
:root { color-scheme: light dark; font-family: system-ui, -apple-system, sans-serif; }
body { margin: 0; padding: 24px 16px; display: flex; justify-content: center; }
main { width: 100%; max-width: 420px; }
h1 { font-size: 22px; font-weight: 600; margin: 0 0 4px; }
.muted { color: GrayText; font-size: 14px; margin: 0 0 20px; }
.row { margin-bottom: 20px; }
.row label { display: flex; justify-content: space-between; font-size: 16px; margin-bottom: 6px; }
input[type=range] { width: 100%; }
.total { display: flex; justify-content: space-between; border-top: 1px solid GrayText;
         padding-top: 12px; font-size: 16px; }
.note { font-size: 13px; color: GrayText; margin-top: 8px; }
.error { color: #d33; font-size: 14px; margin-bottom: 16px; }
button { width: 100%; margin-top: 20px; padding: 12px; font-size: 16px; border-radius: 10px; }
a { display: block; text-align: center; margin-top: 16px; font-size: 14px; }
"""

_SCRIPT = """
const sliders = document.querySelectorAll('input[type=range]');
function update() {
  let total = 0;
  sliders.forEach(s => {
    document.getElementById('out_' + s.dataset.subject).textContent = s.value + ' h';
    total += parseFloat(s.value);
  });
  document.getElementById('total').textContent = total + ' h';
}
sliders.forEach(s => s.addEventListener('input', update));
"""


def render(view: GoalsView) -> str:
    rows = []
    for s in view.subjects:
        value = hours(view.values.get(s.subject, Decimal(0)))
        key = escape(s.subject)
        rows.append(
            f'<div class="row"><label for="goal_{key}"><span>{escape(s.label)}</span>'
            f'<span id="out_{key}">{value} h</span></label>'
            f'<input type="range" id="goal_{key}" name="goal_{key}" data-subject="{key}" '
            f'min="0" max="{hours(MAX_HOURS)}" step="{hours(STEP)}" value="{value}"></div>'
        )
    total = hours(sum(view.values.values(), Decimal(0)))
    status = "Saved" if view.saved else "Suggested from last week, not saved yet"
    if not view.saved and not view.values:
        status = "No goals yet"
    planned = "no goal" if view.last_week_planned is None else f"planned {hours(view.last_week_planned)} h"
    error = f'<p class="error">{escape(view.error)}</p>' if view.error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Goals · {week_label(view.week_start)}</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
<h1>Goals · {week_label(view.week_start)}</h1>
<p class="muted">Last week: {planned}, did {hours(view.last_week_done)} h</p>
{error}
<form method="post" action="/goals">
<input type="hidden" name="week_start" value="{view.week_start.isoformat()}">
{''.join(rows)}
<div class="total"><span>Study total</span><strong id="total">{total} h</strong></div>
<p class="note">{status}</p>
<button type="submit">Save goals</button>
</form>
<a href="{escape(view.dashboard_url)}">Back to dashboard</a>
</main>
<script>{_SCRIPT}</script>
</body>
</html>"""
```

Note: when the page is pre-filled from last week, `view.values` holds last week's goals. When there are no goals at all, `view.values` is empty and every slider shows 0.

- [ ] **Step 4: Modify `ingestor/src/lifelog/app.py`**

Replace the import block at the top with:

```python
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from lifelog import db, goals
from lifelog.config import Settings
from lifelog.goals_page import GoalsView, render
from lifelog.pipeline import close_stale_runs
from lifelog.service import sync_once
```

Add this function after `_timer_loop`:

```python
def _same_origin(request: Request) -> bool:
    """CSRF protection: a form post must come from a page on this same host."""
    source = request.headers.get("origin") or request.headers.get("referer")
    host = request.headers.get("host")
    return bool(source and host) and urlsplit(source).netloc == host
```

Inside `create_app`, after the `/sync` route and before `return app`, add:

```python
    def _goals_view(conn, error: str | None = None) -> GoalsView:
        week = goals.current_week_start(conn)
        last_week = week - timedelta(days=7)
        saved = goals.load_goals(conn, week)
        previous = goals.load_goals(conn, last_week)
        return GoalsView(
            week_start=week,
            subjects=goals.list_subjects(conn),
            values=saved or previous,
            saved=bool(saved),
            last_week_planned=sum(previous.values(), Decimal(0)) if previous else None,
            last_week_done=goals.study_hours(conn, last_week),
            dashboard_url=settings.dashboard_url,
            error=error,
        )

    def _save_goals(form: dict[str, list[str]]) -> Response:
        with db.connect(settings.database_url) as conn:
            try:
                try:
                    week = date.fromisoformat(form.get("week_start", [""])[0])
                except ValueError:
                    raise goals.GoalError("Invalid week") from None
                raw = {key.removeprefix("goal_"): values[0] for key, values in form.items() if key.startswith("goal_")}
                goals.save_goals(conn, week, goals.parse_targets(raw, goals.list_subjects(conn)))
            except goals.GoalError as exc:
                return HTMLResponse(render(_goals_view(conn, error=str(exc))), status_code=400)
        return RedirectResponse(settings.dashboard_url, status_code=303)

    @app.get("/goals", response_class=HTMLResponse)
    def goals_form() -> HTMLResponse:
        with db.connect(settings.database_url) as conn:
            return HTMLResponse(render(_goals_view(conn)))

    @app.post("/goals")
    async def goals_submit(request: Request) -> Response:
        if not _same_origin(request):
            return PlainTextResponse("Forbidden: the form must be sent from this site.", status_code=403)
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        return await asyncio.to_thread(_save_goals, form)
```

- [ ] **Step 5: Run tests**

Run: `python -m uv run pytest tests/test_goals_http.py -v` then `python -m uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add ingestor/src/lifelog/goals_page.py ingestor/src/lifelog/app.py ingestor/tests/test_goals_http.py
git commit -m "feat(ingestor): add weekly goals page with sliders"
```

---

### Task 5: Login on the sync host

**Files:**
- Modify: `caddy/Caddyfile`, `compose.yaml`, `.env.example`, `docs/setup.md`

- [ ] **Step 1: `caddy/Caddyfile` — replace the `sync.` block**

```caddyfile
sync.{$LIFELOG_DOMAIN} {
	# Login for the goals page and the sync button. The IP allowlist comes in M4.
	basic_auth {
		{$SYNC_BASIC_AUTH_USER} {$SYNC_BASIC_AUTH_HASH}
	}
	reverse_proxy ingestor:8000
}
```

- [ ] **Step 2: `compose.yaml` — caddy `environment`, add below `LIFELOG_DOMAIN`**

```yaml
      SYNC_BASIC_AUTH_USER: ${SYNC_BASIC_AUTH_USER:?set in .env}
      SYNC_BASIC_AUTH_HASH: ${SYNC_BASIC_AUTH_HASH:?set in .env}
```

- [ ] **Step 3: `.env.example` — add after the `TIMETAGGER_TOKEN` block**

```bash
# Login for https://sync.<domain> (goals page and Sync now).
# Hash the password with: docker run --rm -it caddy:2 caddy hash-password
# Keep the single quotes: the hash contains '$' characters.
SYNC_BASIC_AUTH_USER=
SYNC_BASIC_AUTH_HASH=''
```

- [ ] **Step 4: `docs/setup.md`**

In §2, add as the last numbered item:

```markdown
5. Choose a login for the goals page and the Sync button: set `SYNC_BASIC_AUTH_USER` (for example your first name), then hash a password and paste it as `SYNC_BASIC_AUTH_HASH='…'` (keep the single quotes):
   ```bash
   docker run --rm -it caddy:2 caddy hash-password
   ```
   Save that password in your password manager: your browser will ask for it once.
```

In §8, add bullets:

```markdown
- Weekly goals: `https://sync.lifelog.lan/goals` (or the **Set goals** link on the dashboard). Set them each Monday; last week's values are offered as a suggestion.
- Study counts entries tagged `#study`, `#ds` / `#ds/<topic>` or `#systemanalysis`, once per entry.
```

At the end of §3, add:

```markdown
**Updating an existing install:** new required `.env` values (such as `SYNC_BASIC_AUTH_USER` / `SYNC_BASIC_AUTH_HASH`) must be added before `docker compose up -d --build`, otherwise compose stops with "set in .env".
```

- [ ] **Step 5: Validate**

In Git Bash, with a scratch directory outside the repo (for example `$TEMP`):

```bash
cp .env.example "$TEMP/test.env"
# edit $TEMP/test.env: every empty value -> x, TIMETAGGER_CREDENTIALS='x:y',
# SYNC_BASIC_AUTH_HASH='<output of: docker run --rm caddy:2 caddy hash-password --plaintext test>'
docker compose --env-file "$TEMP/test.env" config --quiet
MSYS_NO_PATHCONV=1 docker run --rm -e LIFELOG_DOMAIN=lifelog.lan -e SYNC_BASIC_AUTH_USER=me \
  -e SYNC_BASIC_AUTH_HASH='<same hash>' -v "$(pwd -W)/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
rm "$TEMP/test.env"
```

Expected: compose prints nothing (exit 0); Caddy prints `Valid configuration`.

- [ ] **Step 6: Commit**

```bash
git add caddy/Caddyfile compose.yaml .env.example docs/setup.md
git commit -m "feat: require a login for the goals page and sync button"
```

---

### Task 6: Study section on the dashboard

**Files:**
- Modify: `grafana/dashboards/this-week.json` (full replacement)

- [ ] **Step 1: Replace `grafana/dashboards/this-week.json`**

The links hard-code `lifelog.lan`; change them here if `LIFELOG_DOMAIN` differs.

```json
{
  "uid": "this-week",
  "title": "This Week",
  "tags": ["lifelog"],
  "schemaVersion": 39,
  "version": 2,
  "editable": true,
  "refresh": "1m",
  "weekStart": "monday",
  "time": { "from": "now/w", "to": "now/w" },
  "links": [
    { "title": "Sync now", "type": "link", "url": "https://sync.lifelog.lan/sync", "icon": "sync", "targetBlank": false },
    { "title": "Set goals", "type": "link", "url": "https://sync.lifelog.lan/goals", "icon": "edit", "targetBlank": false }
  ],
  "panels": [
    {
      "id": 10,
      "type": "stat",
      "title": "Study",
      "gridPos": { "x": 0, "y": 0, "w": 8, "h": 5 },
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
      "targets": [
        {
          "refId": "A",
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
          "editorMode": "code",
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT hours AS \"Study\" FROM mart.v_study_week"
        }
      ],
      "options": {
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "/^Study$/", "values": false },
        "textMode": "value",
        "colorMode": "none",
        "graphMode": "none",
        "justifyMode": "center"
      },
      "fieldConfig": { "defaults": { "unit": "h", "decimals": 1 }, "overrides": [] }
    },
    {
      "id": 11,
      "type": "stat",
      "title": "This week",
      "gridPos": { "x": 8, "y": 0, "w": 16, "h": 5 },
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
      "targets": [
        {
          "refId": "A",
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
          "editorMode": "code",
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT summary AS \"Week\" FROM mart.v_study_week"
        }
      ],
      "options": {
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "/^Week$/", "values": true },
        "textMode": "value",
        "colorMode": "none",
        "graphMode": "none",
        "justifyMode": "center",
        "text": { "valueSize": 20 }
      }
    },
    {
      "id": 12,
      "type": "timeseries",
      "title": "Study vs goal pace",
      "gridPos": { "x": 0, "y": 5, "w": 24, "h": 10 },
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
      "targets": [
        {
          "refId": "A",
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
          "editorMode": "code",
          "format": "time_series",
          "rawQuery": true,
          "rawSql": "SELECT time, actual_hours AS \"Study\", pace_hours AS \"Goal pace\" FROM mart.v_study_week_cumulative ORDER BY time"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "unit": "h",
          "decimals": 1,
          "min": 0,
          "color": { "mode": "fixed", "fixedColor": "blue" },
          "custom": { "lineWidth": 2, "fillOpacity": 10, "showPoints": "never", "spanNulls": false }
        },
        "overrides": [
          {
            "matcher": { "id": "byName", "options": "Goal pace" },
            "properties": [
              { "id": "custom.lineStyle", "value": { "fill": "dash", "dash": [10, 10] } },
              { "id": "color", "value": { "mode": "fixed", "fixedColor": "#8e8e8e" } },
              { "id": "custom.fillOpacity", "value": 0 },
              { "id": "custom.lineWidth", "value": 1 }
            ]
          }
        ]
      },
      "options": {
        "legend": { "showLegend": true, "displayMode": "list", "placement": "bottom" },
        "tooltip": { "mode": "multi", "sort": "none" }
      }
    },
    {
      "id": 13,
      "type": "stat",
      "title": "Subjects",
      "gridPos": { "x": 0, "y": 15, "w": 24, "h": 4 },
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
      "targets": [
        {
          "refId": "A",
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
          "editorMode": "code",
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT display AS \"Subject\" FROM mart.v_study_subject_week ORDER BY sort_order"
        }
      ],
      "options": {
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "/^Subject$/", "values": true },
        "textMode": "value",
        "colorMode": "none",
        "graphMode": "none",
        "justifyMode": "center",
        "text": { "valueSize": 18 }
      }
    },
    {
      "id": 1,
      "type": "stat",
      "title": "Last sync",
      "gridPos": { "x": 0, "y": 19, "w": 24, "h": 3 },
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
      "targets": [
        {
          "refId": "A",
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
          "editorMode": "code",
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT CASE status WHEN 'success' THEN '✅ ' WHEN 'failed' THEN '❌ ' ELSE '⏳ ' END || status || ' · ' || minutes_ago || ' min ago' || coalesce(' · ' || error, '') AS \"Last sync\" FROM mart.v_sync_status WHERE source = 'timetagger'"
        }
      ],
      "options": {
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "/^Last sync$/", "values": true },
        "textMode": "value",
        "colorMode": "none",
        "justifyMode": "center"
      }
    },
    {
      "id": 2,
      "type": "barchart",
      "title": "Hours per tag this week",
      "gridPos": { "x": 0, "y": 22, "w": 24, "h": 10 },
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
      "targets": [
        {
          "refId": "A",
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
          "editorMode": "code",
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT tag, hours FROM mart.v_week_tag_hours ORDER BY hours DESC"
        }
      ],
      "options": {
        "orientation": "horizontal",
        "xField": "tag",
        "showValue": "always",
        "legend": { "showLegend": false }
      },
      "fieldConfig": { "defaults": { "unit": "h", "decimals": 1 }, "overrides": [] }
    }
  ]
}
```

- [ ] **Step 2: Validate**

Run: `python -c "import json; json.load(open('grafana/dashboards/this-week.json', encoding='utf-8')); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add grafana/dashboards/this-week.json
git commit -m "feat(grafana): add Study section with goal pace to This Week"
```

---

### Task 7: End-to-end check on the laptop (with the user)

- [ ] **Step 1:** User adds `SYNC_BASIC_AUTH_USER` and `SYNC_BASIC_AUTH_HASH` to `.env` (setup guide §2 item 5).
- [ ] **Step 2:** `docker compose up -d --build` — ingestor logs `applied migrations: ['005_study_goals.sql']`; all services running.
- [ ] **Step 3:** `https://sync.lifelog.lan/goals` asks for the login, then shows both subjects with sliders at 0 and "No goals yet".
- [ ] **Step 4:** Set DS and Algorithms 8, System Analysis 4 → Save → lands on the dashboard.
- [ ] **Step 5:** Dashboard Study section shows the week total, `… · goal 12 h · <status>`, the solid Study line with the dashed pace line, and `DS and Algorithms · x / 8 h`, `System Analysis · y / 4 h`.
- [ ] **Step 6:** Sending the form from another origin is refused (covered by tests; optional manual check with `curl -X POST ... -H "Origin: https://evil.example"` → 403 after login).
