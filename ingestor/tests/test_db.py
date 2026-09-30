import psycopg
import pytest
from conftest import DB_DIR

from lifelog import db

MIGRATIONS = sorted(p.name for p in (DB_DIR / "migrations").glob("*.sql"))


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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


def test_setup_allows_migration_that_alters_a_column_used_by_a_view(empty_db, tmp_path):
    write(tmp_path / "migrations" / "001.sql", "CREATE SCHEMA IF NOT EXISTS core; CREATE TABLE core.t (x int);")
    write(tmp_path / "views" / "010.sql", "CREATE VIEW mart.v AS SELECT x FROM core.t;")
    db.setup(empty_db, tmp_path)

    write(tmp_path / "migrations" / "002.sql", "ALTER TABLE core.t ALTER COLUMN x TYPE bigint;")
    assert db.setup(empty_db, tmp_path) == ["002.sql"]
    assert table_exists(empty_db, "mart.v")

    # The bug setup() avoids: migrating while the view still exists.
    write(tmp_path / "migrations" / "003.sql", "ALTER TABLE core.t ALTER COLUMN x TYPE int;")
    with pytest.raises(psycopg.errors.FeatureNotSupported):
        db.migrate(empty_db, tmp_path)


def test_failed_migration_leaves_no_trace(empty_db, tmp_path):
    write(tmp_path / "migrations" / "001.sql", "CREATE SCHEMA core; CREATE TABLE core.t (x int);")
    write(tmp_path / "migrations" / "002.sql", "CREATE TABLE core.u (y int); SELECT * FROM does_not_exist;")

    with pytest.raises(psycopg.errors.UndefinedTable):
        db.migrate(empty_db, tmp_path)

    assert not table_exists(empty_db, "core.u")
    assert empty_db.execute("SELECT filename FROM ops.schema_migration").fetchall() == [("001.sql",)]


def test_broken_view_keeps_previous_mart(empty_db, tmp_path):
    db.migrate(empty_db, DB_DIR)
    write(tmp_path / "views" / "010_v.sql", "CREATE VIEW mart.v AS SELECT 1 AS a;")
    db.rebuild_mart(empty_db, tmp_path)

    write(tmp_path / "views" / "010_v.sql", "CREATE VIEW mart.v AS SELECT * FROM does_not_exist;")
    with pytest.raises(psycopg.Error):
        db.rebuild_mart(empty_db, tmp_path)

    assert empty_db.execute("SELECT a FROM mart.v").fetchone() == (1,)


def test_sql_files_with_bom_are_accepted(empty_db, tmp_path):
    db.migrate(empty_db, DB_DIR)
    write(tmp_path / "views" / "010_v.sql", "﻿CREATE VIEW mart.v AS SELECT 1 AS a;")
    db.rebuild_mart(empty_db, tmp_path)
    assert empty_db.execute("SELECT a FROM mart.v").fetchone() == (1,)


def test_wait_for_db_gives_up_after_deadline():
    sleeps = []
    with pytest.raises(psycopg.OperationalError):
        db.wait_for_db("postgresql://u:p@127.0.0.1:1/x", timeout_seconds=0, sleep=sleeps.append)
    assert sleeps == []


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
