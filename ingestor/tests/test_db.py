from conftest import DB_DIR

from lifelog import db

MIGRATIONS = ["001_schemas.sql", "002_raw.sql", "003_core.sql", "004_ops.sql"]


def table_exists(conn, qualified_name):
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (qualified_name,)).fetchone()[0]


def test_migrate_applies_all_files_in_order(empty_db):
    assert db.migrate(empty_db, DB_DIR) == MIGRATIONS
    for name in ["raw.timetagger_record", "core.activity", "core.activity_tag", "ops.sync_state", "ops.sync_run"]:
        assert table_exists(empty_db, name), name


def test_migrate_is_idempotent(empty_db):
    db.migrate(empty_db, DB_DIR)
    assert db.migrate(empty_db, DB_DIR) == []


def test_rebuild_mart_creates_views_and_grants_reader(empty_db, tmp_path):
    db.migrate(empty_db, DB_DIR)
    (tmp_path / "views").mkdir()
    (tmp_path / "views" / "010_v.sql").write_text("CREATE VIEW mart.v AS SELECT 1 AS a, 2 AS b;")

    db.rebuild_mart(empty_db, tmp_path)

    assert empty_db.execute("SELECT a, b FROM mart.v").fetchone() == (1, 2)
    assert empty_db.execute("SELECT has_table_privilege('grafana_ro', 'mart.v', 'SELECT')").fetchone()[0]


def test_rebuild_mart_allows_removing_columns(empty_db, tmp_path):
    """The reason for drop-and-rebuild: CREATE OR REPLACE VIEW cannot drop columns."""
    db.migrate(empty_db, DB_DIR)
    views = tmp_path / "views"
    views.mkdir()
    (views / "010_v.sql").write_text("CREATE VIEW mart.v AS SELECT 1 AS a, 2 AS b;")
    db.rebuild_mart(empty_db, tmp_path)

    (views / "010_v.sql").write_text("CREATE VIEW mart.v AS SELECT 1 AS a;")
    db.rebuild_mart(empty_db, tmp_path)

    columns = empty_db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'mart' AND table_name = 'v'"
    ).fetchall()
    assert columns == [("a",)]


def test_rebuild_mart_with_real_views(empty_db):
    db.migrate(empty_db, DB_DIR)
    db.rebuild_mart(empty_db, DB_DIR)
    assert table_exists(empty_db, "mart.v_week_tag_hours")
    assert table_exists(empty_db, "mart.v_sync_status")
