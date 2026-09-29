from pathlib import Path

import psycopg
import pytest
from testcontainers.community.postgres import PostgresContainer

from lifelog import db

DB_DIR = Path(__file__).resolve().parents[2] / "db"


@pytest.fixture(scope="session")
def pg_url():
    with PostgresContainer("postgres:17", driver=None) as pg:
        url = pg.get_connection_url()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("CREATE ROLE grafana_ro LOGIN PASSWORD 'test'")
        yield url


@pytest.fixture
def empty_db(pg_url):
    """A connection to a database with none of our schemas."""
    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS mart, core, raw, ops CASCADE")
        yield conn


@pytest.fixture
def conn(empty_db):
    """A connection to a fully migrated database with the real mart views."""
    db.migrate(empty_db, DB_DIR)
    db.rebuild_mart(empty_db, DB_DIR)
    return empty_db
