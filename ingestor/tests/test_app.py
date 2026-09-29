from fastapi.testclient import TestClient

from conftest import DB_DIR
from lifelog import app as app_module
from lifelog.config import Settings
from lifelog.pipeline import SyncResult


def settings(database_url="postgresql://unused/db", db_dir="/unused"):
    return Settings(
        database_url=database_url,
        timetagger_api_url="http://tt/timetagger/api/v2",
        timetagger_token="tok",
        sync_interval_seconds=120,
        dashboard_url="https://dash.lifelog.lan/d/this-week",
        db_dir=db_dir,
    )


def client_for(s):
    # No `with` block: the lifespan (setup, timer) does not run in these tests.
    return TestClient(app_module.create_app(s, start_timer=False))


def test_sync_redirects_to_dashboard_on_success(monkeypatch):
    calls = []

    def fake_sync_once(s, *, trigger, wait_for_lock):
        calls.append((trigger, wait_for_lock))
        return SyncResult(status="success", n_records=2)

    monkeypatch.setattr(app_module, "sync_once", fake_sync_once)
    response = client_for(settings()).get("/sync", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "https://dash.lifelog.lan/d/this-week"
    assert calls == [("button", True)]


def test_sync_reports_failure(monkeypatch):
    monkeypatch.setattr(
        app_module, "sync_once", lambda s, **kw: SyncResult(status="failed", error="TimeTaggerAuthError: HTTP 401")
    )
    response = client_for(settings()).get("/sync", follow_redirects=False)
    assert response.status_code == 502
    assert "HTTP 401" in response.text


def test_health_checks_database(pg_url):
    response = client_for(settings(pg_url)).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_startup_sets_up_database_and_closes_stale_runs(empty_db, pg_url):
    app = app_module.create_app(settings(pg_url, str(DB_DIR)), start_timer=False)
    with TestClient(app):  # runs the lifespan: setup + close_stale_runs
        pass
    assert empty_db.execute("SELECT to_regclass('mart.v_sync_status') IS NOT NULL").fetchone()[0]

    empty_db.execute("INSERT INTO ops.sync_run (source, trigger, status) VALUES ('timetagger', 'timer', 'running')")
    with TestClient(app):
        pass
    assert empty_db.execute("SELECT status FROM ops.sync_run").fetchone()[0] == "failed"
