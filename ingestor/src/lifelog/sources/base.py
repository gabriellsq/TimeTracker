from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import psycopg


@dataclass(frozen=True)
class ActivityIn:
    """One activity in the source-agnostic shape that core.activity stores."""

    source: str
    source_id: str
    started_at: datetime
    ended_at: datetime | None  # None = still running
    description: str
    is_deleted: bool
    tags: tuple[str, ...]
    source_updated_at: datetime | None


@dataclass(frozen=True)
class FetchResult:
    records: list[dict[str, Any]]
    new_cursor: str
    reset: bool


class Source(Protocol):
    """A data source. Each source owns its raw table; the pipeline owns core."""

    name: str

    def fetch(self, cursor: str | None) -> FetchResult: ...

    def upsert_raw(self, conn: psycopg.Connection, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Store records in the raw table; return only those that were new or changed."""
        ...

    def to_activity(self, record: dict[str, Any]) -> ActivityIn: ...
