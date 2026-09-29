import base64
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
import psycopg
from psycopg.types.json import Jsonb

from lifelog.sources.base import ActivityIn, FetchResult
from lifelog.tags import parse_tags


class TimeTaggerError(Exception):
    """TimeTagger could not be reached or answered with a server error."""


class TimeTaggerAuthError(TimeTaggerError):
    """The token was rejected. Retrying cannot fix this."""


class TimeTaggerClient:
    def __init__(
        self,
        api_url: str,
        token: str,
        *,
        http: httpx.Client | None = None,
        retries: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._token = token
        self._http = http or httpx.Client(timeout=30)
        self._retries = retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep

    def __enter__(self) -> "TimeTaggerClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._http.close()

    def get_updates(self, since: float) -> dict[str, Any]:
        return self._request("GET", "updates", params={"since": since}, token=self._token).json()

    def put_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("PUT", "records", json=records, token=self._token).json()

    def login(self, username: str, password: str) -> str:
        payload = {"method": "usernamepassword", "username": username, "password": password}
        body = base64.b64encode(json.dumps(payload).encode())
        return self._request("POST", "bootstrap_authentication", content=body).json()["token"]

    def get_api_token(self, webtoken: str) -> str:
        return self._request("GET", "apitoken", token=webtoken).json()["token"]

    def _request(self, method: str, path: str, *, token: str | None = None, **kwargs: Any) -> httpx.Response:
        headers = {"authtoken": token} if token else {}
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = self._http.request(method, f"{self._api_url}/{path}", headers=headers, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code in (401, 403):
                    raise TimeTaggerAuthError(f"HTTP {response.status_code}: {response.text[:200]}")
                if response.status_code < 500:
                    response.raise_for_status()
                    return response
                last_error = TimeTaggerError(f"HTTP {response.status_code}: {response.text[:200]}")
            if attempt < self._retries:
                self._sleep(self._backoff_seconds * 2**attempt)
        raise TimeTaggerError(f"TimeTagger failed after {self._retries + 1} attempts: {last_error}") from last_error


class UpdatesClient(Protocol):
    def get_updates(self, since: float) -> dict[str, Any]: ...


def _utc(seconds: int | float) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


class TimeTaggerSource:
    name = "timetagger"

    def __init__(self, client: UpdatesClient) -> None:
        self._client = client

    def fetch(self, cursor: str | None) -> FetchResult:
        data = self._client.get_updates(float(cursor) if cursor else 0.0)
        return FetchResult(
            records=list(data.get("records", [])),
            new_cursor=str(float(data["server_time"])),
            reset=bool(data.get("reset", 0)),
        )

    def upsert_raw(
        self, conn: psycopg.Connection, records: list[dict[str, Any]], *, force: bool = False
    ) -> list[dict[str, Any]]:
        changed = []
        with conn.cursor() as cur:
            for rec in records:
                cur.execute(
                    """
                    INSERT INTO raw.timetagger_record (key, payload, server_ts)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (key) DO UPDATE
                        SET payload = EXCLUDED.payload,
                            server_ts = EXCLUDED.server_ts,
                            fetched_at = now()
                        WHERE %s OR raw.timetagger_record.server_ts < EXCLUDED.server_ts
                    RETURNING key
                    """,
                    (str(rec["key"]), Jsonb(rec), float(rec["st"]), force),
                )
                if cur.fetchone() is not None:
                    changed.append(rec)
        return changed

    def to_activity(self, record: dict[str, Any]) -> ActivityIn:
        t1, t2 = int(record["t1"]), int(record["t2"])
        if t2 < t1:
            raise ValueError(f"record {record['key']} ends before it starts (t1={t1}, t2={t2})")
        ds = record.get("ds") or ""
        mt = record.get("mt")
        return ActivityIn(
            source=self.name,
            source_id=str(record["key"]),
            started_at=_utc(t1),
            ended_at=None if t1 == t2 else _utc(t2),
            description=ds,
            is_deleted=ds.startswith("HIDDEN"),
            tags=tuple(parse_tags(ds)),
            source_updated_at=_utc(mt) if mt is not None else None,
        )
