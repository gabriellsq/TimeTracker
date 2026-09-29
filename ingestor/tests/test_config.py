import pytest

from lifelog.config import Settings

REQUIRED = {
    "DATABASE_URL": "postgresql://u:p@db/lifelog",
    "TIMETAGGER_API_URL": "http://timetagger:80/timetagger/api/v2/",
    "DASHBOARD_URL": "https://dash.lifelog.lan/d/this-week",
}


def test_from_env_reads_required_values_and_defaults():
    s = Settings.from_env(REQUIRED)
    assert s.database_url == "postgresql://u:p@db/lifelog"
    assert s.timetagger_api_url == "http://timetagger:80/timetagger/api/v2"  # trailing slash stripped
    assert s.timetagger_token == ""
    assert s.sync_interval_seconds == 120
    assert s.dashboard_url == "https://dash.lifelog.lan/d/this-week"
    assert s.db_dir == "/app/db"


def test_from_env_overrides_defaults():
    env = REQUIRED | {"TIMETAGGER_TOKEN": "abc", "SYNC_INTERVAL_SECONDS": "30", "DB_DIR": "/x/db"}
    s = Settings.from_env(env)
    assert (s.timetagger_token, s.sync_interval_seconds, s.db_dir) == ("abc", 30, "/x/db")


def test_from_env_missing_required_raises():
    with pytest.raises(KeyError):
        Settings.from_env({"DATABASE_URL": "x"})


def test_repr_hides_secrets():
    env = REQUIRED | {"DATABASE_URL": "postgresql://u:s3cret@db/lifelog", "TIMETAGGER_TOKEN": "tok123"}
    text = repr(Settings.from_env(env))
    assert "s3cret" not in text
    assert "tok123" not in text
