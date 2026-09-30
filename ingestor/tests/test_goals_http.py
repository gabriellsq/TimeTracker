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
