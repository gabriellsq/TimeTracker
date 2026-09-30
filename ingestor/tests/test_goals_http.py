from datetime import timedelta

from fastapi.testclient import TestClient

from lifelog import app as app_module
from lifelog import goals
from lifelog.config import Settings

DASHBOARD = "https://dash.lifelog.lan/d/this-week"
SAME_ORIGIN = {"Origin": "http://testserver"}
FORM = {"Content-Type": "application/x-www-form-urlencoded"}


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


def post(pg_url, data=None, headers=SAME_ORIGIN, **kwargs):
    return client_for(pg_url).post("/goals", data=data, headers=headers, follow_redirects=False, **kwargs)


def week(conn):
    return goals.current_week_start(conn).isoformat()


def full(conn, ds="8", systemanalysis="4"):
    return {"week_start": week(conn), "goal_ds": ds, "goal_systemanalysis": systemanalysis}


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
    current = goals.current_week_start(conn)
    conn.execute(
        "INSERT INTO core.goal (week_start, subject, target_hours) VALUES (%s, 'ds', 6.5), (%s, 'systemanalysis', 3)",
        (current, current),
    )
    page = client_for(pg_url).get("/goals")
    assert 'value="6.5"' in page.text
    assert "Saved" in page.text
    assert "not saved yet" not in page.text


def test_partially_saved_week_fills_the_rest_from_last_week(conn, pg_url):
    current = goals.current_week_start(conn)
    conn.execute("INSERT INTO core.goal (week_start, subject, target_hours) VALUES (%s, 'ds', 6)", (current,))
    conn.execute(
        "INSERT INTO core.goal (week_start, subject, target_hours) VALUES (%s, 'systemanalysis', 4)",
        (current - timedelta(days=7),),
    )
    page = client_for(pg_url).get("/goals")
    assert 'value="6"' in page.text and 'value="4"' in page.text
    assert "Some subjects are not saved yet" in page.text


def test_post_saves_goals_and_redirects(conn, pg_url):
    response = post(pg_url, full(conn))
    assert response.status_code == 303
    assert response.headers["location"] == DASHBOARD
    assert saved(conn) == {"ds": 8.0, "systemanalysis": 4.0}


def test_second_post_overwrites(conn, pg_url):
    post(pg_url, full(conn, "8", "4"))
    post(pg_url, full(conn, "6", "2"))
    assert saved(conn) == {"ds": 6.0, "systemanalysis": 2.0}


def test_post_rejects_bad_value(conn, pg_url):
    response = post(pg_url, full(conn, ds="8.3"))
    assert response.status_code == 400
    assert "steps of 0.5" in response.text
    assert saved(conn) == {}


def test_error_keeps_submitted_values(conn, pg_url):
    response = post(pg_url, full(conn, ds="7.5", systemanalysis="8.3"))
    assert response.status_code == 400
    assert 'value="7.5"' in response.text


def test_post_rejects_invalid_week(conn, pg_url):
    response = post(pg_url, {"week_start": "soon", "goal_ds": "1", "goal_systemanalysis": "1"})
    assert response.status_code == 400


def test_past_week_explains_new_week(conn, pg_url):
    last_week = goals.current_week_start(conn) - timedelta(days=7)
    response = post(pg_url, full(conn) | {"week_start": last_week.isoformat()})
    assert response.status_code == 400
    assert "a new week has started" in response.text


def test_missing_subject_is_rejected(conn, pg_url):
    response = post(pg_url, {"week_start": week(conn), "goal_ds": "8"})
    assert response.status_code == 400
    assert "Missing subject" in response.text
    assert saved(conn) == {}


def test_duplicate_field_is_rejected(conn, pg_url):
    wk = week(conn)
    response = post(
        pg_url,
        content=f"week_start={wk}&goal_ds=1&goal_ds=2&goal_systemanalysis=1",
        data=None,
        headers=SAME_ORIGIN | FORM,
    )
    assert response.status_code == 400
    assert saved(conn) == {}


def test_post_rejects_foreign_origin(conn, pg_url):
    response = post(pg_url, full(conn), headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert saved(conn) == {}


def test_post_rejects_missing_origin(conn, pg_url):
    response = post(pg_url, full(conn), headers={})
    assert response.status_code == 403


def test_referer_from_same_host_is_accepted(conn, pg_url):
    response = post(pg_url, full(conn), headers={"Referer": "http://testserver/goals"})
    assert response.status_code == 303


def test_referer_from_other_host_is_rejected(conn, pg_url):
    response = post(pg_url, full(conn), headers={"Referer": "http://evil.example/goals"})
    assert response.status_code == 403


def test_origin_null_is_rejected(conn, pg_url):
    response = post(pg_url, full(conn), headers={"Origin": "null"})
    assert response.status_code == 403


def test_origin_host_comparison_ignores_case(conn, pg_url):
    response = post(pg_url, full(conn), headers={"Origin": "http://TestServer"})
    assert response.status_code == 303


def test_oversized_form_is_rejected(conn, pg_url):
    response = post(pg_url, content="x=" + "a" * 20000, data=None, headers=SAME_ORIGIN | FORM)
    assert response.status_code == 413


def test_invalid_encoding_is_rejected(conn, pg_url):
    response = post(pg_url, content=b"week_start=\xff", data=None, headers=SAME_ORIGIN | FORM)
    assert response.status_code == 400


def test_goals_page_sends_security_headers(conn, pg_url):
    page = client_for(pg_url).get("/goals")
    assert page.headers["X-Frame-Options"] == "DENY"
    assert page.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]


def test_page_escapes_labels(conn, pg_url):
    conn.execute("UPDATE core.subject SET label = '<b>DS</b>' WHERE subject = 'ds'")
    page = client_for(pg_url).get("/goals")
    assert "<b>DS</b>" not in page.text
    assert "&lt;b&gt;DS&lt;/b&gt;" in page.text
