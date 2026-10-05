# Forgotten Timer Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flag timers that ran (or are running) longer than 6 hours on the This Week dashboard, so forgotten timers get fixed in TimeTagger.

**Architecture:** One mart view `mart.v_long_timers` computes flagged activities and a display line each. A "Timer check" stat tile at the top of the dashboard lists them, or shows "✅ No long timers".

**Tech Stack:** PostgreSQL 17 view, Grafana 13 provisioned dashboard JSON, pytest + testcontainers.

**Spec:** `docs/superpowers/specs/2026-10-05-forgotten-timer-guard-design.md`

---

## Environment notes

- `uv` is not on PATH: `python -m uv ...`. Tests: `cd ingestor && python -m uv run pytest -q` (Docker running; testcontainers `postgres:17`).
- Never delete `.venv`, caches, `uv.lock` or tracked files. Edit with an editor/Write/Edit tool (not ad-hoc Python edit scripts). UTF-8, LF.
- Fixtures: `conn` = fully set-up DB (all migrations and mart views), autocommit superuser; role `grafana_ro` exists.
- Commits without any attribution trailer.

---

### Task 1: The `v_long_timers` view

**Files:**
- Create: `db/views/040_v_long_timers.sql`
- Test: `ingestor/tests/test_long_timers.py`

- [ ] **Step 1: Write the failing tests — `ingestor/tests/test_long_timers.py`**

```python
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
```

The file contains "⚠️", "·" and "—": save it as UTF-8.

- [ ] **Step 2: Run to verify failure**

Run (in `ingestor/`): `python -m uv run pytest tests/test_long_timers.py -v`
Expected: FAIL (`relation "mart.v_long_timers" does not exist`).

- [ ] **Step 3: Create `db/views/040_v_long_timers.sql`**

```sql
-- Suspiciously long timers ("forgot to stop?"):
--   running for more than 6 hours, or
--   finished within the last 7 days after more than 6 hours.
-- Tag an entry #long in TimeTagger to mark it as intentional.
-- The 6-hour limit lives here until M3's per-activity limits; dates use America/Vancouver until M2.
CREATE VIEW mart.v_long_timers AS
SELECT f.*,
       CASE WHEN f.running
            THEN '⚠️ Running ' || floor(f.hours)::int || ' h '
                 || lpad(floor((f.hours - floor(f.hours)) * 60)::int::text, 2, '0') || ' m · '
                 || f.tags || ' — forgot to stop?'
            ELSE '⚠️ ' || to_char(round(f.hours, 1), 'FM990.0') || ' h on '
                 || to_char(f.started_at AT TIME ZONE 'America/Vancouver', 'Dy Mon FMDD') || ' · '
                 || f.tags || ' — fix in TimeTagger or tag #long'
       END AS display
FROM (
    SELECT a.activity_id,
           a.started_at,
           a.ended_at,
           a.ended_at IS NULL AS running,
           extract(epoch FROM coalesce(a.ended_at, now()) - a.started_at) / 3600 AS hours,
           coalesce(string_agg('#' || t.tag, ' ' ORDER BY t.position), '(no tags)') AS tags
    FROM core.activity a
    LEFT JOIN core.activity_tag t USING (activity_id)
    WHERE NOT a.is_deleted
      AND coalesce(a.ended_at, now()) - a.started_at > interval '6 hours'
      AND (a.ended_at IS NULL OR a.ended_at > now() - interval '7 days')
    GROUP BY a.activity_id, a.started_at, a.ended_at
    HAVING NOT coalesce(bool_or(t.tag = 'long'), false)
) f;
```

Note: `lpad(..., 2, '0')` makes minutes two digits (`7 h 05 m`). The test cases use 12 and 30 minutes, so they are unaffected.

- [ ] **Step 4: Run tests**

Run: `python -m uv run pytest tests/test_long_timers.py -v` then `python -m uv run pytest -q`
Expected: all pass. If a display assertion fails, fix the SQL (not the test) and describe the fix.

- [ ] **Step 5: Commit**

```bash
git add db/views/040_v_long_timers.sql ingestor/tests/test_long_timers.py
git commit -m "feat(db): flag timers running or lasting over 6 hours"
```

---

### Task 2: "Timer check" tile on the dashboard

**Files:**
- Modify: `grafana/dashboards/this-week.json`
- Modify: `docs/setup.md`

- [ ] **Step 1: Add the tile.** In `grafana/dashboards/this-week.json`, insert this object as the **first** element of `"panels"`:

```json
    {
      "id": 20,
      "type": "stat",
      "title": "Timer check",
      "gridPos": { "x": 0, "y": 0, "w": 24, "h": 3 },
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
      "targets": [
        {
          "refId": "A",
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
          "editorMode": "code",
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT display AS \"Timer check\" FROM mart.v_long_timers ORDER BY started_at"
        }
      ],
      "options": {
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "/^Timer check$/", "values": true },
        "textMode": "value",
        "colorMode": "none",
        "graphMode": "none",
        "justifyMode": "center",
        "text": { "valueSize": 18 }
      },
      "fieldConfig": { "defaults": { "noValue": "✅ No long timers" }, "overrides": [] }
    },
```

- [ ] **Step 2: Move every other panel down by 3.** Change only the `"y"` of each existing panel's `gridPos`:

| Panel (id) | old y | new y |
|---|---|---|
| Study (10) | 0 | 3 |
| This week (11) | 0 | 3 |
| Study vs goal pace (12) | 5 | 8 |
| Subjects (13) | 15 | 18 |
| Last sync (1) | 19 | 22 |
| Hours per tag this week (2) | 22 | 25 |

- [ ] **Step 3: `docs/setup.md` §8**, add a bullet:

```markdown
- **Timer check** (top of the dashboard) warns about timers running or lasting over 6 hours. Fix the entry in TimeTagger, or add `#long` to a genuinely long session to silence the warning.
```

- [ ] **Step 4: Validate**

Run (repo root): `python -c "import json; d=json.load(open('grafana/dashboards/this-week.json', encoding='utf-8')); print([(p['title'], p['gridPos']['y']) for p in d['panels']])"`
Expected: `Timer check` at y 0, then the others at 3, 3, 8, 18, 22, 25.

Run (in `ingestor/`): `python -m uv run pytest -q`
Expected: all pass. `tests/test_dashboard.py` runs the new tile's query as `grafana_ro` and checks its field regex.

- [ ] **Step 5: Commit**

```bash
git add grafana/dashboards/this-week.json docs/setup.md
git commit -m "feat(grafana): add Timer check tile to This Week"
```

---

### Task 3: Check on the laptop (with the user)

- [ ] `docker compose up -d --build`. Logs show the views rebuilt, and no new migration.
- [ ] The dashboard top shows `✅ No long timers`, or the current offenders.
- [ ] Start a TimeTagger timer back-dated by 7 hours (or check the view with a test row). The tile shows `⚠️ Running 7 h … — forgot to stop?`. Stop or fix it, press Sync now, and the warning disappears.
