from lifelog import cli
from lifelog.pipeline import SyncResult

ENV = {
    "DATABASE_URL": "postgresql://unused/db",
    "TIMETAGGER_API_URL": "http://tt/timetagger/api/v2",
    "DASHBOARD_URL": "https://dash.lifelog.lan/d/this-week",
}


def test_sync_command_prints_result_and_exits_zero(monkeypatch, capsys):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "sync_once", lambda s, **kw: SyncResult(status="success", n_records=3))

    assert cli.main(["sync"]) == 0
    assert "success: 3 records" in capsys.readouterr().out


def test_sync_command_exits_nonzero_on_failure(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "sync_once", lambda s, **kw: SyncResult(status="failed", error="x"))

    assert cli.main(["sync"]) == 1
