# M1 Walking Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An activity logged in TimeTagger on the iPhone (over HTTPS) appears in a Grafana panel within 3 minutes, or immediately after pressing Sync.

**Architecture:** Docker Compose stack: `postgres`, `timetagger`, `ingestor` (Python/FastAPI), `grafana`, `caddy`. The ingestor pulls changes from TimeTagger's `GET /api/v2/updates?since=<cursor>` every 2 minutes (or on `GET /sync`), stores payloads in `raw`, normalizes them into `core` in the same transaction as the cursor update, and rebuilds the `mart` views on startup. Grafana reads only `mart` (and `ops`) through a read-only role. Caddy is the only exposed service, with a local CA (`tls internal`).

**Tech Stack:** Python 3.12, uv, FastAPI, uvicorn, httpx, psycopg 3, pytest, testcontainers; PostgreSQL 17; TimeTagger; Grafana OSS; Caddy 2; Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-28-lifelog-sprint1-design.md` (§3.1 M1).

---

## Verified facts about TimeTagger (from its source, 2026-09-28)

- API base: `http://<host>/timetagger/api/v2/`. Auth header: `authtoken`.
- `GET updates?since=<float>` → `{"server_time": float, "reset": int, "records": [...], "settings": [...]}`. Records with `st >= since` are returned (inclusive, so the boundary record comes back every time — upserts must be idempotent).
- Record fields: `key` (str), `t1`, `t2`, `mt` (int unix seconds), `ds` (str, optional), `st` (float, server time).
- Running record: `t1 == t2`. Hidden (deleted) record: `ds` starts with `"HIDDEN"`.
- TimeTagger does **not** validate `t2 >= t1`. We must.
- Tags: `#` followed by `[0-9A-Za-z_/-]` or any non-ASCII char; lowercased. `#a#b` is two tags. `/` is allowed (`#study/math`).
- Login: `POST bootstrap_authentication`, body = base64 of `{"method": "usernamepassword", "username", "password"}` → `{"token": <webtoken>}`. `GET apitoken` with a **webtoken** → `{"token": <apitoken>}`.
- `PUT records` with a JSON list → `{"accepted": [...], "failed": [...], "errors": [...]}`.
- Docker image `ghcr.io/almarklein/timetagger`, env `TIMETAGGER_BIND`, `TIMETAGGER_DATADIR`, `TIMETAGGER_LOG_LEVEL`, `TIMETAGGER_CREDENTIALS` (`user:bcrypthash`). Example credentials `test:test` = `test:$2a$08$0CD1NFiIbancwWsu3se1v.RNR/b7YeZd71yg3cZ/3whGlyU6Iny5i`.

## Deviations from the spec (additive, M1 only)

- `ops.sync_run` gets an extra `n_skipped integer` column.
- Logs are plain text in M1; structured JSON logs are deferred.
- The M1 mart view hard-codes `America/Vancouver`; `core.local_tz()` replaces it in M2.
- Parse-error records are logged and counted in `ops.sync_run`, not yet written to `ops.dq_result` (M3).

## Prerequisites (on the development machine)

- Docker running (Docker Desktop on Windows/Mac, Docker Engine on Fedora). Tests use testcontainers and need it.
- `uv` installed (`pip install uv` or https://docs.astral.sh/uv/).
- Optional: `just` (commands below also work without it).

## File structure

```
TimeTracker/
├── .gitattributes                         # force LF (shell scripts break with CRLF in Linux containers)
├── .gitignore
├── .env.example
├── justfile
├── compose.yaml
├── caddy/Caddyfile
├── scripts/hash_password.py               # bcrypt hash for TIMETAGGER_CREDENTIALS
├── db/
│   ├── init/01-roles.sh                   # roles + grants, first start only
│   ├── migrations/001_schemas.sql
│   ├── migrations/002_raw.sql
│   ├── migrations/003_core.sql
│   ├── migrations/004_ops.sql
│   ├── views/010_v_week_tag_hours.sql     # provisional, replaced in M2
│   └── views/020_v_sync_status.sql
├── grafana/
│   ├── provisioning/datasources/lifelog.yaml
│   ├── provisioning/dashboards/lifelog.yaml
│   └── dashboards/this-week.json
├── ingestor/
│   ├── pyproject.toml
│   ├── uv.lock                            # generated
│   ├── Dockerfile
│   ├── src/lifelog/
│   │   ├── __init__.py
│   │   ├── config.py                      # Settings from env
│   │   ├── tags.py                        # parse_tags()
│   │   ├── db.py                          # connect, wait_for_db, migrate, rebuild_mart
│   │   ├── pipeline.py                    # run_sync(): lock, transaction, cursor, run log
│   │   ├── service.py                     # sync_once(): wires settings → client → source → pipeline
│   │   ├── app.py                         # FastAPI: /health, /sync, timer loop
│   │   ├── cli.py                         # lifelog sync | get-token
│   │   └── sources/
│   │       ├── __init__.py
│   │       ├── base.py                    # ActivityIn, FetchResult, Source protocol
│   │       └── timetagger.py              # TimeTaggerClient, TimeTaggerSource
│   └── tests/
│       ├── conftest.py
│       ├── test_config.py
│       ├── test_tags.py
│       ├── test_timetagger_client.py
│       ├── test_timetagger_source.py
│       ├── test_db.py
│       ├── test_pipeline.py
│       ├── test_views.py
│       ├── test_app.py
│       ├── test_cli.py
│       └── test_timetagger_integration.py
└── docs/setup.md
```

All commands below are run from the repository root unless stated otherwise.

---

### Task 1: Repository scaffolding

**Files:**
- Create: `.gitattributes`, `.env.example`, `justfile`, `scripts/hash_password.py`
- Modify: `.gitignore`

- [ ] **Step 1: Create `.gitattributes`**

```gitattributes
# Everything in this repo runs in Linux containers: keep LF line endings,
# otherwise shell scripts fail with "$'\r': command not found".
* text=auto eol=lf
*.png binary
*.jpg binary
```

- [ ] **Step 2: Replace `.gitignore`**

```gitignore
.env
__pycache__/
.venv/
*.pyc
.pytest_cache/
caddy-root.crt
```

- [ ] **Step 3: Create `.env.example`**

```bash
# Copy to .env and fill in. Never commit .env.
# Passwords must be URL-safe. Generate each one with:
#   python -c "import secrets; print(secrets.token_hex(24))"

LIFELOG_DOMAIN=lifelog.lan

POSTGRES_PASSWORD=
INGESTOR_DB_PASSWORD=
GRAFANA_RO_DB_PASSWORD=
GRAFANA_ADMIN_PASSWORD=

# Generate with: uv run --with bcrypt python scripts/hash_password.py
# Keep the single quotes: the hash contains '$' characters.
TIMETAGGER_CREDENTIALS=''

# Generate after the first start (see docs/setup.md):
#   docker compose exec -it ingestor lifelog get-token --username <you>
TIMETAGGER_TOKEN=

SYNC_INTERVAL_SECONDS=120
DASHBOARD_URL=https://dash.lifelog.lan/d/this-week
```

- [ ] **Step 4: Create `scripts/hash_password.py`**

```python
"""Print a TIMETAGGER_CREDENTIALS line for .env.

Run locally so the password never leaves this machine:
    uv run --with bcrypt python scripts/hash_password.py
"""

import getpass

import bcrypt


def main() -> None:
    username = input("TimeTagger username: ").strip()
    password = getpass.getpass("TimeTagger password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("Passwords do not match.")
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    print(f"TIMETAGGER_CREDENTIALS='{username}:{hashed}'")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Create `justfile`**

```just
set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# All tests (needs Docker)
test:
    uv run --directory ingestor pytest

# Tests without the real TimeTagger container
test-fast:
    uv run --directory ingestor pytest -m "not integration"

up:
    docker compose up -d --build

down:
    docker compose down

logs service="ingestor":
    docker compose logs -f {{service}}
```

- [ ] **Step 6: Renormalize line endings and commit**

```bash
git add --renormalize .
git add .gitattributes .gitignore .env.example justfile scripts/hash_password.py
git commit -m "chore: add repo scaffolding, env template and helper scripts"
```

---

### Task 2: Ingestor project skeleton and settings

**Files:**
- Create: `ingestor/pyproject.toml`, `ingestor/src/lifelog/__init__.py`, `ingestor/src/lifelog/sources/__init__.py`, `ingestor/src/lifelog/config.py`
- Test: `ingestor/tests/test_config.py`

- [ ] **Step 1: Create `ingestor/pyproject.toml`**

```toml
[project]
name = "lifelog"
version = "0.1.0"
description = "Lifelog ingestor: TimeTagger -> PostgreSQL"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "httpx>=0.27",
    "psycopg[binary]>=3.2",
]

[project.scripts]
lifelog = "lifelog.cli:main"

[dependency-groups]
dev = [
    "pytest>=8",
    "testcontainers[postgres]>=4.8",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/lifelog"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "integration: runs a real TimeTagger container (slow)",
]
```

- [ ] **Step 2: Create empty package files**

`ingestor/src/lifelog/__init__.py`:
```python
```

`ingestor/src/lifelog/sources/__init__.py`:
```python
```

- [ ] **Step 3: Write the failing test** — `ingestor/tests/test_config.py`

```python
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
```

- [ ] **Step 4: Install dependencies and run the test to verify it fails**

```bash
cd ingestor
uv sync
uv run pytest tests/test_config.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'lifelog.config'`.

- [ ] **Step 5: Implement** — `ingestor/src/lifelog/config.py`

```python
import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    timetagger_api_url: str
    timetagger_token: str
    sync_interval_seconds: int
    dashboard_url: str
    db_dir: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        return cls(
            database_url=env["DATABASE_URL"],
            timetagger_api_url=env["TIMETAGGER_API_URL"].rstrip("/"),
            timetagger_token=env.get("TIMETAGGER_TOKEN", ""),
            sync_interval_seconds=int(env.get("SYNC_INTERVAL_SECONDS", "120")),
            dashboard_url=env["DASHBOARD_URL"],
            db_dir=env.get("DB_DIR", "/app/db"),
        )
```

- [ ] **Step 6: Run the test to verify it passes**

Run (in `ingestor/`): `uv run pytest tests/test_config.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit** (from repo root)

```bash
git add ingestor/pyproject.toml ingestor/uv.lock ingestor/src ingestor/tests/test_config.py
git commit -m "feat(ingestor): add project skeleton and settings"
```

---

### Task 3: Tag parser

**Files:**
- Create: `ingestor/src/lifelog/tags.py`
- Test: `ingestor/tests/test_tags.py`

- [ ] **Step 1: Write the failing test** — `ingestor/tests/test_tags.py`

```python
from lifelog.tags import parse_tags


def test_tags_in_order_of_appearance_without_hash():
    assert parse_tags("#study #math reviewing chapter 3") == ["study", "math"]


def test_tags_are_lowercased():
    assert parse_tags("#Study #DeepWork") == ["study", "deepwork"]


def test_duplicate_tags_keep_first_position():
    assert parse_tags("#math #study #Math") == ["math", "study"]


def test_glued_tags_are_split():
    assert parse_tags("#study#math") == ["study", "math"]


def test_allowed_characters_follow_timetagger():
    assert parse_tags("#study/math #deep-work #leisure_reading") == [
        "study/math",
        "deep-work",
        "leisure_reading",
    ]


def test_non_ascii_characters_are_part_of_the_tag():
    assert parse_tags("#café, then #academia.") == ["café", "academia"]


def test_standalone_hash_and_empty_input_give_no_tags():
    assert parse_tags("# nothing here") == []
    assert parse_tags("") == []
    assert parse_tags(None) == []


def test_hidden_prefix_does_not_affect_tags():
    assert parse_tags("HIDDEN #gym") == ["gym"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run (in `ingestor/`): `uv run pytest tests/test_tags.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lifelog.tags'`.

- [ ] **Step 3: Implement** — `ingestor/src/lifelog/tags.py`

```python
import re

# Mirrors TimeTagger's is_valid_tag_charcode(): letters, digits, '-', '_', '/'
# and any non-ASCII character. A '#' ends the current tag and starts a new one.
_TAG_RE = re.compile(r"#([0-9A-Za-z_/\-\u0080-\U0010ffff]+)")


def parse_tags(ds: str | None) -> list[str]:
    """Tags in order of first appearance, lowercased, without '#', de-duplicated."""
    seen: dict[str, None] = {}
    for match in _TAG_RE.finditer(ds or ""):
        seen.setdefault(match.group(1).lower(), None)
    return list(seen)
```

- [ ] **Step 4: Run the test to verify it passes**

Run (in `ingestor/`): `uv run pytest tests/test_tags.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add ingestor/src/lifelog/tags.py ingestor/tests/test_tags.py
git commit -m "feat(ingestor): parse TimeTagger tags in order, lowercased, de-duplicated"
```

---

### Task 4: TimeTagger HTTP client

**Files:**
- Create: `ingestor/src/lifelog/sources/timetagger.py`
- Test: `ingestor/tests/test_timetagger_client.py`

- [ ] **Step 1: Write the failing test** — `ingestor/tests/test_timetagger_client.py`

```python
import base64
import json

import httpx
import pytest

from lifelog.sources.timetagger import (
    TimeTaggerAuthError,
    TimeTaggerClient,
    TimeTaggerError,
)

API = "http://tt/timetagger/api/v2"


def make_client(handler, token="tok", retries=3):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return TimeTaggerClient(API, token, http=http, retries=retries, sleep=lambda s: None)


def test_get_updates_sends_token_and_since():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["since"] = request.url.params["since"]
        seen["token"] = request.headers["authtoken"]
        return httpx.Response(200, json={"server_time": 10.5, "reset": 0, "records": [], "settings": []})

    with make_client(handler) as client:
        data = client.get_updates(3.25)

    assert data["server_time"] == 10.5
    assert seen == {"path": "/timetagger/api/v2/updates", "since": "3.25", "token": "tok"}


def test_server_errors_are_retried_then_succeed():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"server_time": 1.0, "reset": 0, "records": [], "settings": []})

    with make_client(handler) as client:
        client.get_updates(0)

    assert len(calls) == 3


def test_gives_up_after_retries():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("refused", request=request)

    with make_client(handler, retries=3) as client, pytest.raises(TimeTaggerError):
        client.get_updates(0)

    assert len(calls) == 4  # 1 attempt + 3 retries


def test_auth_error_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="bad token")

    with make_client(handler) as client, pytest.raises(TimeTaggerAuthError):
        client.get_updates(0)

    assert len(calls) == 1


def test_login_posts_base64_json_and_returns_webtoken():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(base64.b64decode(request.content))
        return httpx.Response(200, json={"token": "web123"})

    with make_client(handler, token="") as client:
        assert client.login("me", "secret") == "web123"

    assert seen["path"] == "/timetagger/api/v2/bootstrap_authentication"
    assert seen["body"] == {"method": "usernamepassword", "username": "me", "password": "secret"}


def test_get_api_token_uses_webtoken():
    def handler(request):
        assert request.url.path == "/timetagger/api/v2/apitoken"
        assert request.headers["authtoken"] == "web123"
        return httpx.Response(200, json={"token": "api456"})

    with make_client(handler, token="") as client:
        assert client.get_api_token("web123") == "api456"


def test_put_records_sends_json_list():
    def handler(request):
        assert request.method == "PUT"
        assert request.url.path == "/timetagger/api/v2/records"
        body = json.loads(request.content)
        return httpx.Response(200, json={"accepted": [r["key"] for r in body], "failed": [], "errors": []})

    with make_client(handler) as client:
        result = client.put_records([{"key": "k1", "t1": 1, "t2": 2, "mt": 2, "ds": "#x"}])

    assert result["accepted"] == ["k1"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run (in `ingestor/`): `uv run pytest tests/test_timetagger_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lifelog.sources.timetagger'`.

- [ ] **Step 3: Implement** — `ingestor/src/lifelog/sources/timetagger.py`

```python
import base64
import json
import time
from collections.abc import Callable
from typing import Any

import httpx


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
```

- [ ] **Step 4: Run the test to verify it passes**

Run (in `ingestor/`): `uv run pytest tests/test_timetagger_client.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add ingestor/src/lifelog/sources/timetagger.py ingestor/tests/test_timetagger_client.py
git commit -m "feat(ingestor): add TimeTagger API client with retries and auth handling"
```

---

### Task 5: Source interface and TimeTagger record mapping

**Files:**
- Create: `ingestor/src/lifelog/sources/base.py`
- Modify: `ingestor/src/lifelog/sources/timetagger.py` (append `TimeTaggerSource`)
- Test: `ingestor/tests/test_timetagger_source.py`

- [ ] **Step 1: Create `ingestor/src/lifelog/sources/base.py`**

```python
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
```

- [ ] **Step 2: Write the failing test** — `ingestor/tests/test_timetagger_source.py`

```python
from datetime import UTC, datetime

import pytest

from lifelog.sources.timetagger import TimeTaggerSource

T0 = 1_790_000_000  # 2026-09-21T13:33:20Z


class FakeClient:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.since_seen = []

    def get_updates(self, since):
        self.since_seen.append(since)
        return self.responses.pop(0)


def record(**overrides):
    base = {"key": "k1", "t1": T0, "t2": T0 + 3600, "mt": T0 + 3600, "ds": "#study #math", "st": 100.5}
    return base | overrides


def test_to_activity_maps_fields_in_utc():
    a = TimeTaggerSource(FakeClient()).to_activity(record())
    assert a.source == "timetagger"
    assert a.source_id == "k1"
    assert a.started_at == datetime.fromtimestamp(T0, UTC)
    assert a.ended_at == datetime.fromtimestamp(T0 + 3600, UTC)
    assert a.source_updated_at == datetime.fromtimestamp(T0 + 3600, UTC)
    assert a.tags == ("study", "math")
    assert a.description == "#study #math"
    assert a.is_deleted is False


def test_running_record_has_no_end():
    a = TimeTaggerSource(FakeClient()).to_activity(record(t2=T0))
    assert a.ended_at is None


def test_hidden_record_is_deleted():
    a = TimeTaggerSource(FakeClient()).to_activity(record(ds="HIDDEN #study"))
    assert a.is_deleted is True
    assert a.tags == ("study",)


def test_missing_description_gives_empty_tags():
    rec = record()
    del rec["ds"]
    a = TimeTaggerSource(FakeClient()).to_activity(rec)
    assert (a.description, a.tags) == ("", ())


def test_end_before_start_is_rejected():
    with pytest.raises(ValueError, match="ends before it starts"):
        TimeTaggerSource(FakeClient()).to_activity(record(t2=T0 - 1))


def test_fetch_without_cursor_starts_from_zero():
    client = FakeClient([{"server_time": 123.25, "reset": 0, "records": [record()], "settings": []}])
    result = TimeTaggerSource(client).fetch(None)
    assert client.since_seen == [0.0]
    assert result.new_cursor == "123.25"
    assert result.records == [record()]
    assert result.reset is False


def test_fetch_uses_cursor_as_since():
    client = FakeClient([{"server_time": 200.0, "reset": 1, "records": [], "settings": []}])
    result = TimeTaggerSource(client).fetch("123.25")
    assert client.since_seen == [123.25]
    assert result.reset is True
```

- [ ] **Step 3: Run the test to verify it fails**

Run (in `ingestor/`): `uv run pytest tests/test_timetagger_source.py -v`
Expected: FAIL with `ImportError: cannot import name 'TimeTaggerSource'`.

- [ ] **Step 4: Implement** — replace the imports at the top of `ingestor/src/lifelog/sources/timetagger.py` with:

```python
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
```

and append at the end of the file:

```python
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

    def upsert_raw(self, conn: psycopg.Connection, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                        WHERE raw.timetagger_record.server_ts < EXCLUDED.server_ts
                    RETURNING key
                    """,
                    (str(rec["key"]), Jsonb(rec), float(rec.get("st", 0))),
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run (in `ingestor/`): `uv run pytest tests/test_timetagger_source.py tests/test_timetagger_client.py -v`
Expected: 14 passed.

- [ ] **Step 6: Commit**

```bash
git add ingestor/src/lifelog/sources/base.py ingestor/src/lifelog/sources/timetagger.py ingestor/tests/test_timetagger_source.py
git commit -m "feat(ingestor): add source interface and TimeTagger record mapping"
```

---

### Task 6: Database init script and migrations

**Files:**
- Create: `db/init/01-roles.sh`, `db/migrations/001_schemas.sql`, `db/migrations/002_raw.sql`, `db/migrations/003_core.sql`, `db/migrations/004_ops.sql`

These files are exercised by the tests in Task 7.

- [ ] **Step 1: Create `db/init/01-roles.sh`**

```bash
#!/usr/bin/env bash
# Run by the postgres image ONLY on first start with an empty data volume
# (/docker-entrypoint-initdb.d). Changing passwords later needs ALTER ROLE by hand.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v ingestor_pw="$INGESTOR_DB_PASSWORD" \
  -v grafana_pw="$GRAFANA_RO_DB_PASSWORD" <<'SQL'
CREATE ROLE ingestor LOGIN PASSWORD :'ingestor_pw';
CREATE ROLE grafana_ro LOGIN PASSWORD :'grafana_pw';
GRANT CONNECT, CREATE ON DATABASE lifelog TO ingestor;
GRANT CONNECT ON DATABASE lifelog TO grafana_ro;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
```

- [ ] **Step 2: Create `db/migrations/001_schemas.sql`**

```sql
-- ops is created by the migration runner itself (it stores ops.schema_migration).
-- mart is dropped and rebuilt from db/views on every start.
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
```

- [ ] **Step 3: Create `db/migrations/002_raw.sql`**

```sql
CREATE TABLE raw.timetagger_record (
    key         text PRIMARY KEY,
    payload     jsonb NOT NULL,
    server_ts   double precision NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);
```

- [ ] **Step 4: Create `db/migrations/003_core.sql`**

```sql
CREATE TABLE core.activity (
    activity_id        bigserial PRIMARY KEY,
    source             text NOT NULL,
    source_id          text NOT NULL,
    started_at         timestamptz NOT NULL,
    ended_at           timestamptz,
    description        text,
    is_deleted         boolean NOT NULL DEFAULT false,
    source_updated_at  timestamptz,
    ingested_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, source_id),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE TABLE core.activity_tag (
    activity_id  bigint NOT NULL REFERENCES core.activity ON DELETE CASCADE,
    tag          text NOT NULL,
    position     smallint NOT NULL,
    PRIMARY KEY (activity_id, tag)
);

CREATE INDEX activity_started_at_idx ON core.activity (started_at);
CREATE INDEX activity_tag_tag_idx ON core.activity_tag (tag);
```

- [ ] **Step 5: Create `db/migrations/004_ops.sql`**

```sql
CREATE TABLE ops.sync_state (
    source           text PRIMARY KEY,
    cursor           text,
    last_success_at  timestamptz,
    last_error       text
);

CREATE TABLE ops.sync_run (
    run_id       bigserial PRIMARY KEY,
    source       text NOT NULL,
    trigger      text NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    n_records    integer,
    n_skipped    integer,
    status       text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    error        text
);

GRANT USAGE ON SCHEMA ops TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA ops TO grafana_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT SELECT ON TABLES TO grafana_ro;
```

- [ ] **Step 6: Commit**

```bash
git add db/init db/migrations
git commit -m "feat(db): add role init script and raw/core/ops migrations"
```

---

### Task 7: Migration runner and mart rebuild

**Files:**
- Create: `ingestor/src/lifelog/db.py`, `ingestor/tests/conftest.py`
- Test: `ingestor/tests/test_db.py`

- [ ] **Step 1: Create `ingestor/tests/conftest.py`**

```python
from pathlib import Path

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

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
```

- [ ] **Step 2: Write the failing test** — `ingestor/tests/test_db.py`

```python
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
```

- [ ] **Step 3: Create the two real mart views** (needed by `test_rebuild_mart_with_real_views`)

`db/views/010_v_week_tag_hours.sql`:
```sql
-- PROVISIONAL (M1): hours per tag in the current local ISO week.
-- No midnight split, timezone hard-coded. Replaced by the star schema in M2.
CREATE VIEW mart.v_week_tag_hours AS
SELECT
    t.tag,
    round(
        sum(extract(epoch FROM coalesce(a.ended_at, now()) - a.started_at))::numeric / 3600,
        2
    ) AS hours
FROM core.activity a
JOIN core.activity_tag t USING (activity_id)
WHERE NOT a.is_deleted
  AND a.started_at >= date_trunc('week', now() AT TIME ZONE 'America/Vancouver') AT TIME ZONE 'America/Vancouver'
GROUP BY t.tag;
```

`db/views/020_v_sync_status.sql`:
```sql
-- Latest sync run per source, for the dashboard status tile.
CREATE VIEW mart.v_sync_status AS
SELECT
    source,
    status,
    started_at,
    finished_at,
    n_records,
    n_skipped,
    error,
    (extract(epoch FROM now() - coalesce(finished_at, started_at)) / 60)::int AS minutes_ago
FROM (
    SELECT DISTINCT ON (source) *
    FROM ops.sync_run
    ORDER BY source, started_at DESC, run_id DESC
) latest;
```

- [ ] **Step 4: Run the test to verify it fails**

Run (in `ingestor/`): `uv run pytest tests/test_db.py -v`
Expected: FAIL with `ImportError: cannot import name 'db' from 'lifelog'`. (The first run also pulls the `postgres:17` image.)

- [ ] **Step 5: Implement** — `ingestor/src/lifelog/db.py`

```python
import time
from collections.abc import Callable
from pathlib import Path

import psycopg
from psycopg import sql


def connect(url: str) -> psycopg.Connection:
    """Autocommit connection: every multi-statement unit of work uses an explicit conn.transaction()."""
    return psycopg.connect(url, autocommit=True)


def wait_for_db(
    url: str, timeout_seconds: float = 60, sleep: Callable[[float], None] = time.sleep
) -> psycopg.Connection:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            return connect(url)
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            sleep(1)


def migrate(conn: psycopg.Connection, db_dir: Path) -> list[str]:
    """Apply db/migrations/*.sql not yet applied, in filename order. Returns the newly applied names."""
    conn.execute("CREATE SCHEMA IF NOT EXISTS ops")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.schema_migration (
            filename    text PRIMARY KEY,
            applied_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    applied = {row[0] for row in conn.execute("SELECT filename FROM ops.schema_migration")}
    newly_applied = []
    for path in sorted((Path(db_dir) / "migrations").glob("*.sql")):
        if path.name in applied:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO ops.schema_migration (filename) VALUES (%s)", (path.name,))
        newly_applied.append(path.name)
    return newly_applied


def rebuild_mart(conn: psycopg.Connection, db_dir: Path, reader_role: str = "grafana_ro") -> None:
    """Drop and recreate the mart schema from db/views/*.sql. Safe: mart holds only views."""
    reader = sql.Identifier(reader_role)
    with conn.transaction():
        conn.execute("DROP SCHEMA IF EXISTS mart CASCADE")
        conn.execute("CREATE SCHEMA mart")
        for path in sorted((Path(db_dir) / "views").glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA mart TO {}").format(reader))
        conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA mart TO {}").format(reader))
```

- [ ] **Step 6: Run the test to verify it passes**

Run (in `ingestor/`): `uv run pytest tests/test_db.py -v`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add ingestor/src/lifelog/db.py ingestor/tests/conftest.py ingestor/tests/test_db.py db/views
git commit -m "feat(ingestor): add migration runner, mart rebuild and M1 mart views"
```

---

### Task 8: Raw upsert for TimeTagger

**Files:**
- Test: `ingestor/tests/test_timetagger_source.py` (append)

`upsert_raw` was implemented in Task 5; this task proves it against a real database.

- [ ] **Step 1: Append the tests** to `ingestor/tests/test_timetagger_source.py`

```python
def raw_rows(conn):
    return conn.execute("SELECT key, server_ts, payload->>'ds' FROM raw.timetagger_record ORDER BY key").fetchall()


def test_upsert_raw_inserts_new_records(conn):
    source = TimeTaggerSource(FakeClient())
    changed = source.upsert_raw(conn, [record(key="a"), record(key="b")])
    assert [r["key"] for r in changed] == ["a", "b"]
    assert raw_rows(conn) == [("a", 100.5, "#study #math"), ("b", 100.5, "#study #math")]


def test_upsert_raw_ignores_same_server_time(conn):
    source = TimeTaggerSource(FakeClient())
    source.upsert_raw(conn, [record()])
    assert source.upsert_raw(conn, [record()]) == []


def test_upsert_raw_replaces_when_server_time_is_newer(conn):
    source = TimeTaggerSource(FakeClient())
    source.upsert_raw(conn, [record()])
    changed = source.upsert_raw(conn, [record(ds="#gym", st=200.0)])
    assert len(changed) == 1
    assert raw_rows(conn) == [("k1", 200.0, "#gym")]


def test_upsert_raw_never_goes_back_in_time(conn):
    source = TimeTaggerSource(FakeClient())
    source.upsert_raw(conn, [record(ds="#gym", st=200.0)])
    assert source.upsert_raw(conn, [record(ds="#old", st=150.0)]) == []
    assert raw_rows(conn) == [("k1", 200.0, "#gym")]
```

- [ ] **Step 2: Run the tests**

Run (in `ingestor/`): `uv run pytest tests/test_timetagger_source.py -v`
Expected: 11 passed. If any fail, fix `upsert_raw` in `sources/timetagger.py`, not the test.

- [ ] **Step 3: Commit**

```bash
git add ingestor/tests/test_timetagger_source.py
git commit -m "test(ingestor): cover TimeTagger raw upsert against Postgres"
```

---

### Task 9: Sync pipeline

**Files:**
- Create: `ingestor/src/lifelog/pipeline.py`
- Test: `ingestor/tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test** — `ingestor/tests/test_pipeline.py`

```python
from datetime import UTC, datetime

import psycopg
import pytest

from lifelog import pipeline
from lifelog.pipeline import SYNC_LOCK_KEY, run_sync
from lifelog.sources.timetagger import TimeTaggerError, TimeTaggerSource

T0 = 1_790_000_000


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.since_seen = []

    def get_updates(self, since):
        self.since_seen.append(since)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def rec(key="k1", t1=T0, t2=T0 + 3600, ds="#study #math", st=100.5):
    return {"key": key, "t1": t1, "t2": t2, "mt": t2, "ds": ds, "st": st}


def updates(*records, server_time=100.5):
    return {"server_time": server_time, "reset": 0, "records": list(records), "settings": []}


def sync(conn, client):
    return run_sync(conn, TimeTaggerSource(client), trigger="test", wait_for_lock=True)


def activities(conn):
    return conn.execute(
        "SELECT source_id, started_at, ended_at, is_deleted FROM core.activity ORDER BY source_id"
    ).fetchall()


def tags(conn, source_id="k1"):
    return conn.execute(
        """
        SELECT t.tag, t.position FROM core.activity_tag t
        JOIN core.activity a USING (activity_id)
        WHERE a.source_id = %s ORDER BY t.position
        """,
        (source_id,),
    ).fetchall()


def cursor(conn):
    row = conn.execute("SELECT cursor FROM ops.sync_state WHERE source = 'timetagger'").fetchone()
    return row[0] if row else None


def last_run(conn):
    return conn.execute(
        "SELECT trigger, status, n_records, n_skipped, error FROM ops.sync_run ORDER BY run_id DESC LIMIT 1"
    ).fetchone()


def test_first_sync_loads_raw_and_core(conn):
    result = sync(conn, FakeClient(updates(rec())))

    assert (result.status, result.n_records) == ("success", 1)
    assert activities(conn) == [
        ("k1", datetime.fromtimestamp(T0, UTC), datetime.fromtimestamp(T0 + 3600, UTC), False)
    ]
    assert tags(conn) == [("study", 1), ("math", 2)]
    assert cursor(conn) == "100.5"
    assert last_run(conn) == ("test", "success", 1, 0, None)


def test_next_sync_starts_from_saved_cursor(conn):
    client = FakeClient(updates(rec()), updates(server_time=150.0))
    sync(conn, client)
    sync(conn, client)
    assert client.since_seen == [0.0, 100.5]
    assert cursor(conn) == "150.0"


def test_repeated_records_are_idempotent(conn):
    sync(conn, FakeClient(updates(rec())))
    result = sync(conn, FakeClient(updates(rec())))
    assert result.n_records == 0
    assert len(activities(conn)) == 1
    assert tags(conn) == [("study", 1), ("math", 2)]


def test_updated_record_replaces_tags(conn):
    sync(conn, FakeClient(updates(rec())))
    sync(conn, FakeClient(updates(rec(ds="#gym", st=200.0), server_time=200.0)))
    assert tags(conn) == [("gym", 1)]


def test_hidden_record_is_soft_deleted(conn):
    sync(conn, FakeClient(updates(rec())))
    sync(conn, FakeClient(updates(rec(ds="HIDDEN #study #math", st=200.0), server_time=200.0)))
    assert activities(conn)[0][3] is True


def test_running_record_has_null_end(conn):
    sync(conn, FakeClient(updates(rec(t2=T0))))
    assert activities(conn)[0][2] is None


def test_bad_record_is_skipped_and_batch_continues(conn):
    result = sync(conn, FakeClient(updates(rec(key="bad", t2=T0 - 60), rec(key="good"))))

    assert result.status == "success"
    assert result.skipped_keys == ["bad"]
    assert [a[0] for a in activities(conn)] == ["good"]
    assert last_run(conn) == ("test", "success", 1, 1, "skipped: bad")


def test_failure_mid_transaction_commits_nothing(conn, monkeypatch):
    def boom(*args):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(pipeline, "_save_cursor", boom)
    result = sync(conn, FakeClient(updates(rec())))

    assert result.status == "failed"
    assert "disk on fire" in result.error
    assert conn.execute("SELECT count(*) FROM raw.timetagger_record").fetchone()[0] == 0
    assert activities(conn) == []
    assert cursor(conn) is None
    assert conn.execute("SELECT last_error FROM ops.sync_state").fetchone()[0].endswith("disk on fire")
    assert last_run(conn)[1] == "failed"


def test_source_error_marks_run_failed(conn):
    result = sync(conn, FakeClient(TimeTaggerError("unreachable")))
    assert result.status == "failed"
    assert last_run(conn)[1:3] == ("failed", 0)


def test_timer_skips_when_another_sync_holds_the_lock(conn, pg_url):
    with psycopg.connect(pg_url, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (SYNC_LOCK_KEY,))
        result = run_sync(conn, TimeTaggerSource(FakeClient()), trigger="timer", wait_for_lock=False)
    assert result.status == "skipped"
    assert conn.execute("SELECT count(*) FROM ops.sync_run").fetchone()[0] == 0


def test_lock_is_released_after_sync(conn, pg_url):
    sync(conn, FakeClient(updates()))
    with psycopg.connect(pg_url, autocommit=True) as other:
        assert other.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,)).fetchone()[0] is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run (in `ingestor/`): `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL with `ImportError: cannot import name 'pipeline' from 'lifelog'`.

- [ ] **Step 3: Implement** — `ingestor/src/lifelog/pipeline.py`

```python
import logging
from dataclasses import dataclass, field

import psycopg

from lifelog.sources.base import ActivityIn, Source

log = logging.getLogger(__name__)

SYNC_LOCK_KEY = 4_242_001  # arbitrary, unique to this app


@dataclass
class SyncResult:
    status: str  # "success" | "failed" | "skipped"
    n_records: int = 0
    skipped_keys: list[str] = field(default_factory=list)
    error: str | None = None


def run_sync(conn: psycopg.Connection, source: Source, *, trigger: str, wait_for_lock: bool) -> SyncResult:
    """One sync run. `conn` must be in autocommit mode (see db.connect).

    Data, raw and cursor are written in ONE transaction: a failure anywhere leaves
    the database unchanged and the next run re-fetches the same changes.
    """
    if wait_for_lock:
        conn.execute("SELECT pg_advisory_lock(%s)", (SYNC_LOCK_KEY,))
    elif not conn.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY,)).fetchone()[0]:
        return SyncResult(status="skipped")
    try:
        run_id = _start_run(conn, source.name, trigger)
        try:
            result = _sync(conn, source)
        except Exception as exc:
            log.exception("sync of %s failed", source.name)
            result = SyncResult(status="failed", error=f"{type(exc).__name__}: {exc}")
            _record_failure(conn, source.name, result.error)
        _finish_run(conn, run_id, result)
        return result
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY,))


def _sync(conn: psycopg.Connection, source: Source) -> SyncResult:
    with conn.transaction():
        fetched = source.fetch(_read_cursor(conn, source.name))
        changed = source.upsert_raw(conn, fetched.records)
        skipped = []
        for record in changed:
            try:
                with conn.transaction():  # savepoint: one bad record never aborts the batch
                    _upsert_activity(conn, source.to_activity(record))
            except Exception as exc:
                key = str(record.get("key"))
                log.warning("skipping record %s from %s: %s", key, source.name, exc)
                skipped.append(key)
        _save_cursor(conn, source.name, fetched.new_cursor)
    return SyncResult(status="success", n_records=len(changed) - len(skipped), skipped_keys=skipped)


def _upsert_activity(conn: psycopg.Connection, a: ActivityIn) -> None:
    activity_id = conn.execute(
        """
        INSERT INTO core.activity
            (source, source_id, started_at, ended_at, description, is_deleted, source_updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, source_id) DO UPDATE SET
            started_at = EXCLUDED.started_at,
            ended_at = EXCLUDED.ended_at,
            description = EXCLUDED.description,
            is_deleted = EXCLUDED.is_deleted,
            source_updated_at = EXCLUDED.source_updated_at,
            ingested_at = now()
        RETURNING activity_id
        """,
        (a.source, a.source_id, a.started_at, a.ended_at, a.description, a.is_deleted, a.source_updated_at),
    ).fetchone()[0]
    conn.execute("DELETE FROM core.activity_tag WHERE activity_id = %s", (activity_id,))
    if a.tags:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO core.activity_tag (activity_id, tag, position) VALUES (%s, %s, %s)",
                [(activity_id, tag, position) for position, tag in enumerate(a.tags, start=1)],
            )


def _read_cursor(conn: psycopg.Connection, source: str) -> str | None:
    row = conn.execute("SELECT cursor FROM ops.sync_state WHERE source = %s", (source,)).fetchone()
    return row[0] if row else None


def _save_cursor(conn: psycopg.Connection, source: str, cursor: str) -> None:
    conn.execute(
        """
        INSERT INTO ops.sync_state (source, cursor, last_success_at, last_error)
        VALUES (%s, %s, now(), NULL)
        ON CONFLICT (source) DO UPDATE
            SET cursor = EXCLUDED.cursor, last_success_at = now(), last_error = NULL
        """,
        (source, cursor),
    )


def _record_failure(conn: psycopg.Connection, source: str, error: str) -> None:
    conn.execute(
        """
        INSERT INTO ops.sync_state (source, last_error) VALUES (%s, %s)
        ON CONFLICT (source) DO UPDATE SET last_error = EXCLUDED.last_error
        """,
        (source, error),
    )


def _start_run(conn: psycopg.Connection, source: str, trigger: str) -> int:
    return conn.execute(
        "INSERT INTO ops.sync_run (source, trigger, status) VALUES (%s, %s, 'running') RETURNING run_id",
        (source, trigger),
    ).fetchone()[0]


def _finish_run(conn: psycopg.Connection, run_id: int, result: SyncResult) -> None:
    error = result.error
    if error is None and result.skipped_keys:
        error = "skipped: " + ", ".join(result.skipped_keys)
    conn.execute(
        """
        UPDATE ops.sync_run
        SET finished_at = now(), status = %s, n_records = %s, n_skipped = %s, error = %s
        WHERE run_id = %s
        """,
        (result.status, result.n_records, len(result.skipped_keys), error, run_id),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run (in `ingestor/`): `uv run pytest tests/test_pipeline.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add ingestor/src/lifelog/pipeline.py ingestor/tests/test_pipeline.py
git commit -m "feat(ingestor): add transactional sync pipeline with advisory lock and run log"
```

---

### Task 10: Mart view behaviour

**Files:**
- Test: `ingestor/tests/test_views.py`

- [ ] **Step 1: Write the tests** — `ingestor/tests/test_views.py`

```python
WEEK_START = "(date_trunc('week', now() AT TIME ZONE 'America/Vancouver') AT TIME ZONE 'America/Vancouver')"


def add_activity(conn, source_id, start_sql, end_sql, tags, deleted=False):
    activity_id = conn.execute(
        f"""
        INSERT INTO core.activity (source, source_id, started_at, ended_at, is_deleted)
        VALUES ('test', %s, {start_sql}, {end_sql}, %s)
        RETURNING activity_id
        """,
        (source_id, deleted),
    ).fetchone()[0]
    for position, tag in enumerate(tags, start=1):
        conn.execute(
            "INSERT INTO core.activity_tag (activity_id, tag, position) VALUES (%s, %s, %s)",
            (activity_id, tag, position),
        )


def week_hours(conn):
    return dict(conn.execute("SELECT tag, hours FROM mart.v_week_tag_hours").fetchall())


def test_counts_this_weeks_hours_per_tag(conn):
    add_activity(conn, "a", f"{WEEK_START} + interval '1 hour'", f"{WEEK_START} + interval '3 hours'", ["study", "math"])
    hours = week_hours(conn)
    assert float(hours["study"]) == 2.0
    assert float(hours["math"]) == 2.0


def test_ignores_last_week_and_deleted(conn):
    add_activity(conn, "old", f"{WEEK_START} - interval '2 hours'", f"{WEEK_START} - interval '1 hour'", ["study"])
    add_activity(conn, "del", f"{WEEK_START} + interval '1 hour'", f"{WEEK_START} + interval '2 hours'", ["gym"], deleted=True)
    assert week_hours(conn) == {}


def test_running_activity_counts_until_now(conn):
    add_activity(conn, "run", "now() - interval '30 minutes'", "NULL", ["study"])
    assert float(week_hours(conn)["study"]) > 0


def test_sync_status_shows_latest_run_per_source(conn):
    conn.execute(
        """
        INSERT INTO ops.sync_run (source, trigger, started_at, finished_at, status, error) VALUES
            ('timetagger', 'timer', now() - interval '10 minutes', now() - interval '10 minutes', 'failed', 'boom'),
            ('timetagger', 'timer', now() - interval '1 minute',  now() - interval '1 minute',  'success', NULL)
        """
    )
    rows = conn.execute("SELECT source, status, minutes_ago FROM mart.v_sync_status").fetchall()
    assert rows == [("timetagger", "success", 1)]


def test_grafana_role_cannot_read_core_or_raw(conn):
    # Checked as the superuser: grafana_ro cannot even resolve names in core/raw.
    for table in ["core.activity", "core.activity_tag", "raw.timetagger_record"]:
        assert conn.execute("SELECT has_table_privilege('grafana_ro', %s, 'SELECT')", (table,)).fetchone()[0] is False


def test_grafana_role_can_read_mart_views(conn):
    conn.execute("SET ROLE grafana_ro")
    try:
        conn.execute("SELECT * FROM mart.v_week_tag_hours").fetchall()
        conn.execute("SELECT * FROM mart.v_sync_status").fetchall()
    finally:
        conn.execute("RESET ROLE")
```

- [ ] **Step 2: Run the tests**

Run (in `ingestor/`): `uv run pytest tests/test_views.py -v`
Expected: 6 passed. If a view test fails, fix the SQL in `db/views/`, not the test.

- [ ] **Step 3: Commit**

```bash
git add ingestor/tests/test_views.py
git commit -m "test(db): cover M1 mart views and read-only role access"
```

---

### Task 11: Service wiring, HTTP app and CLI

**Files:**
- Create: `ingestor/src/lifelog/service.py`, `ingestor/src/lifelog/app.py`, `ingestor/src/lifelog/cli.py`
- Test: `ingestor/tests/test_app.py`, `ingestor/tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

`ingestor/tests/test_app.py`:
```python
from fastapi.testclient import TestClient

from lifelog import app as app_module
from lifelog.config import Settings
from lifelog.pipeline import SyncResult


def settings(database_url="postgresql://unused/db"):
    return Settings(
        database_url=database_url,
        timetagger_api_url="http://tt/timetagger/api/v2",
        timetagger_token="tok",
        sync_interval_seconds=120,
        dashboard_url="https://dash.lifelog.lan/d/this-week",
        db_dir="/unused",
    )


def client_for(s):
    # No `with` block: the lifespan (migrations, timer) does not run in these tests.
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
```

`ingestor/tests/test_cli.py`:
```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (in `ingestor/`): `uv run pytest tests/test_app.py tests/test_cli.py -v`
Expected: FAIL with `ImportError: cannot import name 'app' from 'lifelog'`.

- [ ] **Step 3: Implement** — `ingestor/src/lifelog/service.py`

```python
from lifelog import db
from lifelog.config import Settings
from lifelog.pipeline import SyncResult, run_sync
from lifelog.sources.timetagger import TimeTaggerClient, TimeTaggerSource


def sync_once(settings: Settings, *, trigger: str, wait_for_lock: bool) -> SyncResult:
    with (
        TimeTaggerClient(settings.timetagger_api_url, settings.timetagger_token) as client,
        db.connect(settings.database_url) as conn,
    ):
        return run_sync(conn, TimeTaggerSource(client), trigger=trigger, wait_for_lock=wait_for_lock)
```

- [ ] **Step 4: Implement** — `ingestor/src/lifelog/app.py`

```python
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, RedirectResponse

from lifelog import db
from lifelog.config import Settings
from lifelog.service import sync_once

log = logging.getLogger("lifelog")


async def _timer_loop(settings: Settings) -> None:
    while True:
        try:
            result = await asyncio.to_thread(sync_once, settings, trigger="timer", wait_for_lock=False)
            log.info("timer sync: %s, %d records", result.status, result.n_records)
        except Exception:
            log.exception("timer sync crashed")
        await asyncio.sleep(settings.sync_interval_seconds)


def create_app(settings: Settings | None = None, *, start_timer: bool = True) -> FastAPI:
    """App factory. Run with: uvicorn --factory lifelog.app:create_app"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_dir = Path(settings.db_dir)
        with db.wait_for_db(settings.database_url) as conn:
            log.info("applied migrations: %s", db.migrate(conn, db_dir) or "none")
            db.rebuild_mart(conn, db_dir)
        task = asyncio.create_task(_timer_loop(settings)) if start_timer else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="lifelog ingestor", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        with db.connect(settings.database_url) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/sync")
    def sync():
        result = sync_once(settings, trigger="button", wait_for_lock=True)
        if result.status == "failed":
            return PlainTextResponse(f"Sync failed: {result.error}", status_code=502)
        return RedirectResponse(settings.dashboard_url, status_code=303)

    return app
```

Note: `test_app.py` patches `lifelog.app.sync_once`; the endpoint looks the name up in the `app` module at call time, so the patch takes effect.

- [ ] **Step 5: Implement** — `ingestor/src/lifelog/cli.py`

```python
import argparse
import getpass

from lifelog.config import Settings
from lifelog.service import sync_once
from lifelog.sources.timetagger import TimeTaggerClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lifelog")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sync", help="run one sync now")
    get_token = commands.add_parser("get-token", help="print a TimeTagger API token for .env")
    get_token.add_argument("--username", required=True)
    args = parser.parse_args(argv)

    settings = Settings.from_env()

    if args.command == "sync":
        result = sync_once(settings, trigger="cli", wait_for_lock=True)
        detail = f" ({result.error})" if result.error else ""
        print(f"{result.status}: {result.n_records} records{detail}")
        return 1 if result.status == "failed" else 0

    password = getpass.getpass("TimeTagger password: ")
    with TimeTaggerClient(settings.timetagger_api_url, token="") as client:
        print(client.get_api_token(client.login(args.username, password)))
    return 0
```

- [ ] **Step 6: Run the tests to verify they pass**

Run (in `ingestor/`): `uv run pytest tests/test_app.py tests/test_cli.py -v`
Expected: 5 passed.

- [ ] **Step 7: Run the whole fast suite**

Run (in `ingestor/`): `uv run pytest -m "not integration" -v`
Expected: all tests pass (≈ 60).

- [ ] **Step 8: Commit**

```bash
git add ingestor/src/lifelog/service.py ingestor/src/lifelog/app.py ingestor/src/lifelog/cli.py ingestor/tests/test_app.py ingestor/tests/test_cli.py
git commit -m "feat(ingestor): add HTTP app with /sync and /health, timer loop and CLI"
```

---

### Task 12: Integration test against a real TimeTagger

**Files:**
- Test: `ingestor/tests/test_timetagger_integration.py`

This test proves our assumptions about TimeTagger's API (login, API token, PUT, updates, running and hidden records) against the real server.

- [ ] **Step 1: Write the test** — `ingestor/tests/test_timetagger_integration.py`

```python
import time
import uuid

import httpx
import pytest
from testcontainers.core.container import DockerContainer

from lifelog.sources.timetagger import TimeTaggerClient, TimeTaggerSource

pytestmark = pytest.mark.integration

# test:test — the example credentials from TimeTagger's own docker-compose file.
TEST_CREDENTIALS = "test:$2a$08$0CD1NFiIbancwWsu3se1v.RNR/b7YeZd71yg3cZ/3whGlyU6Iny5i"


@pytest.fixture(scope="module")
def api_url():
    container = (
        DockerContainer("ghcr.io/almarklein/timetagger")
        .with_env("TIMETAGGER_BIND", "0.0.0.0:80")
        .with_env("TIMETAGGER_DATADIR", "/root/_timetagger")
        .with_env("TIMETAGGER_CREDENTIALS", TEST_CREDENTIALS)
        .with_exposed_ports(80)
    )
    with container:
        base = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(80)}"
        deadline = time.monotonic() + 60
        while True:
            try:
                if httpx.get(f"{base}/timetagger/", timeout=2).status_code < 500:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("TimeTagger did not start within 60 s")
            time.sleep(1)
        yield f"{base}/timetagger/api/v2"


@pytest.fixture(scope="module")
def api_token(api_url):
    with TimeTaggerClient(api_url, token="") as client:
        return client.get_api_token(client.login("test", "test"))


def test_put_then_fetch_updates(api_url, api_token):
    now = int(time.time())
    done, running, hidden = (uuid.uuid4().hex[:8] for _ in range(3))
    with TimeTaggerClient(api_url, api_token) as client:
        result = client.put_records(
            [
                {"key": done, "t1": now - 3600, "t2": now - 60, "mt": now, "ds": "#study #math"},
                {"key": running, "t1": now - 600, "t2": now - 600, "mt": now, "ds": "#gym"},
                {"key": hidden, "t1": now - 7200, "t2": now - 3600, "mt": now, "ds": "HIDDEN #rest"},
            ]
        )
        assert sorted(result["accepted"]) == sorted([done, running, hidden])

        fetched = TimeTaggerSource(client).fetch(None)

    assert float(fetched.new_cursor) > 0
    by_key = {r["key"]: r for r in fetched.records}
    assert by_key[done]["st"] > 0

    source = TimeTaggerSource(client)
    assert source.to_activity(by_key[done]).tags == ("study", "math")
    assert source.to_activity(by_key[running]).ended_at is None
    assert source.to_activity(by_key[hidden]).is_deleted is True


def test_bad_token_is_an_auth_error(api_url):
    from lifelog.sources.timetagger import TimeTaggerAuthError

    with TimeTaggerClient(api_url, "not-a-token") as client, pytest.raises(TimeTaggerAuthError):
        client.get_updates(0)
```

- [ ] **Step 2: Run it**

Run (in `ingestor/`): `uv run pytest -m integration -v`
Expected: 2 passed. If `login` or `get_api_token` fail, read the response body in the error, adjust `TimeTaggerClient` to match the real API, update the unit test in `test_timetagger_client.py` to the corrected behaviour, and re-run both suites.

- [ ] **Step 3: Commit**

```bash
git add ingestor/tests/test_timetagger_integration.py
git commit -m "test(ingestor): verify TimeTagger API assumptions against a real container"
```

---

### Task 13: Ingestor container image

**Files:**
- Create: `ingestor/Dockerfile`, `ingestor/.dockerignore`

- [ ] **Step 1: Create `ingestor/.dockerignore`**

```
.venv
.pytest_cache
__pycache__
tests
```

- [ ] **Step 2: Create `ingestor/Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "--factory", "lifelog.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Build it**

Run: `docker build -t lifelog-ingestor ingestor`
Expected: `Successfully tagged lifelog-ingestor:latest` (or `naming to docker.io/library/lifelog-ingestor` with BuildKit).

- [ ] **Step 4: Smoke-test the CLI inside the image**

Run: `docker run --rm lifelog-ingestor lifelog --help`
Expected: usage text listing `sync` and `get-token`.

- [ ] **Step 5: Commit**

```bash
git add ingestor/Dockerfile ingestor/.dockerignore
git commit -m "build(ingestor): add container image"
```

---

### Task 14: Compose stack, Caddy and Grafana provisioning

**Files:**
- Create: `compose.yaml`, `caddy/Caddyfile`, `grafana/provisioning/datasources/lifelog.yaml`, `grafana/provisioning/dashboards/lifelog.yaml`, `grafana/dashboards/this-week.json`

- [ ] **Step 1: Create `compose.yaml`**

```yaml
name: lifelog

services:
  postgres:
    image: postgres:17
    restart: unless-stopped
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set in .env}
      POSTGRES_DB: lifelog
      INGESTOR_DB_PASSWORD: ${INGESTOR_DB_PASSWORD:?set in .env}
      GRAFANA_RO_DB_PASSWORD: ${GRAFANA_RO_DB_PASSWORD:?set in .env}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d lifelog"]
      interval: 5s
      timeout: 5s
      retries: 20
    # No "ports:" — Postgres is reachable only on the internal compose network.

  timetagger:
    image: ghcr.io/almarklein/timetagger
    restart: unless-stopped
    environment:
      TIMETAGGER_BIND: 0.0.0.0:80
      TIMETAGGER_DATADIR: /root/_timetagger
      TIMETAGGER_LOG_LEVEL: info
      TIMETAGGER_CREDENTIALS: ${TIMETAGGER_CREDENTIALS:?set in .env}
    volumes:
      - timetagger_data:/root/_timetagger

  ingestor:
    build: ./ingestor
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql://ingestor:${INGESTOR_DB_PASSWORD}@postgres:5432/lifelog
      TIMETAGGER_API_URL: http://timetagger:80/timetagger/api/v2
      TIMETAGGER_TOKEN: ${TIMETAGGER_TOKEN:-}
      SYNC_INTERVAL_SECONDS: ${SYNC_INTERVAL_SECONDS:-120}
      DASHBOARD_URL: ${DASHBOARD_URL:?set in .env}
      DB_DIR: /app/db
    volumes:
      - ./db:/app/db:ro
    depends_on:
      postgres:
        condition: service_healthy
      timetagger:
        condition: service_started

  grafana:
    image: grafana/grafana-oss
    restart: unless-stopped
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:?set in .env}
      GF_SERVER_ROOT_URL: https://dash.${LIFELOG_DOMAIN:?set in .env}/
      GF_AUTH_ANONYMOUS_ENABLED: "false"
      GF_ANALYTICS_REPORTING_ENABLED: "false"
      GF_ANALYTICS_CHECK_FOR_UPDATES: "false"
      GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES: "false"
      GF_NEWS_NEWS_FEED_ENABLED: "false"
      GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH: /var/lib/grafana/dashboards/this-week.json
      GRAFANA_RO_DB_PASSWORD: ${GRAFANA_RO_DB_PASSWORD:?set in .env}
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - ./grafana/dashboards:/var/lib/grafana/dashboards:ro
    depends_on:
      postgres:
        condition: service_healthy

  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "443:443"   # the only port exposed to the LAN
    environment:
      LIFELOG_DOMAIN: ${LIFELOG_DOMAIN:?set in .env}
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data      # holds the local root CA: must persist (spec §5)
      - caddy_config:/config
    depends_on:
      - timetagger
      - grafana
      - ingestor

volumes:
  pgdata:
  timetagger_data:
  grafana_data:
  caddy_data:
  caddy_config:
```

- [ ] **Step 2: Create `caddy/Caddyfile`**

```caddyfile
{
	# Issue all certificates from Caddy's own local CA (no internet, no public DNS).
	local_certs
}

tt.{$LIFELOG_DOMAIN} {
	reverse_proxy timetagger:80
}

dash.{$LIFELOG_DOMAIN} {
	reverse_proxy grafana:3000
}

sync.{$LIFELOG_DOMAIN} {
	# M4 adds basic_auth and the IP allowlist here.
	reverse_proxy ingestor:8000
}
```

- [ ] **Step 3: Create `grafana/provisioning/datasources/lifelog.yaml`**

```yaml
apiVersion: 1

datasources:
  - name: lifelog
    uid: lifelog-pg
    type: grafana-postgresql-datasource
    url: postgres:5432
    user: grafana_ro
    isDefault: true
    editable: false
    jsonData:
      database: lifelog
      sslmode: disable
      timescaledb: false
    secureJsonData:
      password: $GRAFANA_RO_DB_PASSWORD
```

- [ ] **Step 4: Create `grafana/provisioning/dashboards/lifelog.yaml`**

```yaml
apiVersion: 1

providers:
  - name: lifelog
    folder: Lifelog
    type: file
    allowUiUpdates: true   # UI edits are allowed but overwritten on reload: export JSON and commit
    options:
      path: /var/lib/grafana/dashboards
```

- [ ] **Step 5: Create `grafana/dashboards/this-week.json`**

The sync link hard-codes `lifelog.lan`; change it here if `LIFELOG_DOMAIN` differs.

```json
{
  "uid": "this-week",
  "title": "This Week",
  "tags": ["lifelog"],
  "schemaVersion": 39,
  "version": 1,
  "editable": true,
  "refresh": "1m",
  "time": { "from": "now-7d", "to": "now" },
  "links": [
    {
      "title": "Sync now",
      "type": "link",
      "url": "https://sync.lifelog.lan/sync",
      "icon": "sync",
      "targetBlank": false
    }
  ],
  "panels": [
    {
      "id": 1,
      "type": "stat",
      "title": "Last sync",
      "gridPos": { "x": 0, "y": 0, "w": 24, "h": 4 },
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
      "targets": [
        {
          "refId": "A",
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
          "editorMode": "code",
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT status, minutes_ago AS \"minutes ago\" FROM mart.v_sync_status WHERE source = 'timetagger'"
        }
      ],
      "options": {
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "/.*/", "values": true },
        "textMode": "value_and_name",
        "colorMode": "none"
      }
    },
    {
      "id": 2,
      "type": "barchart",
      "title": "Hours per tag this week",
      "gridPos": { "x": 0, "y": 4, "w": 24, "h": 12 },
      "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
      "targets": [
        {
          "refId": "A",
          "datasource": { "type": "grafana-postgresql-datasource", "uid": "lifelog-pg" },
          "editorMode": "code",
          "format": "table",
          "rawQuery": true,
          "rawSql": "SELECT tag, hours FROM mart.v_week_tag_hours ORDER BY hours DESC"
        }
      ],
      "options": {
        "orientation": "horizontal",
        "xField": "tag",
        "showValue": "always",
        "legend": { "showLegend": false }
      },
      "fieldConfig": { "defaults": { "unit": "h", "decimals": 1 }, "overrides": [] }
    }
  ]
}
```

- [ ] **Step 6: Validate the compose file**

Create a throwaway `.env` for validation only (it is git-ignored):

```bash
cp .env.example .env
```

Fill every empty value in `.env` with a placeholder like `x` (except keep `TIMETAGGER_CREDENTIALS='x:y'`), then run:

Run: `docker compose config --quiet`
Expected: no output, exit code 0.

- [ ] **Step 7: Commit**

```bash
git add compose.yaml caddy grafana
git commit -m "feat: add compose stack with Caddy and provisioned Grafana dashboard"
```

---

### Task 15: Setup guide

**Files:**
- Create: `docs/setup.md`

- [ ] **Step 1: Create `docs/setup.md`**

````markdown
# Setup

## 1. Server prerequisites (Fedora)

```bash
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager addrepo --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo
sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"   # log out and back in afterwards
```

On Windows/macOS for development: Docker Desktop, Colima, Rancher Desktop or Podman Desktop.

## 2. Configure `.env`

```bash
cp .env.example .env
```

1. Fill `POSTGRES_PASSWORD`, `INGESTOR_DB_PASSWORD`, `GRAFANA_RO_DB_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, each with the output of:
   ```bash
   python -c "import secrets; print(secrets.token_hex(24))"
   ```
2. Generate `TIMETAGGER_CREDENTIALS` locally (your password never leaves the machine) and paste the printed line into `.env`:
   ```bash
   uv run --with bcrypt python scripts/hash_password.py
   ```
3. Leave `TIMETAGGER_TOKEN` empty for now.

## 3. Start the stack

```bash
docker compose up -d --build
docker compose ps
```

All services should be `running`; `postgres` and `ingestor` should become `healthy`.
Until the token is set, `docker compose logs ingestor` shows authentication errors — expected.

## 4. Name resolution

The sites are `tt.lifelog.lan`, `dash.lifelog.lan`, `sync.lifelog.lan`.

- **Router (needed for the iPhone):** add local DNS entries for the three names pointing at the server's LAN IP. If the router cannot do this, a local DNS server (AdGuard Home) is needed — raise it before continuing.
- **Laptop (quick alternative for testing):** add to the hosts file (`C:\Windows\System32\drivers\etc\hosts` on Windows, `/etc/hosts` elsewhere):
  ```
  <server-ip>  tt.lifelog.lan dash.lifelog.lan sync.lifelog.lan
  ```
  Use `127.0.0.1` when running the stack on the laptop itself.

## 5. Trust Caddy's local certificate authority

Export the root certificate (run on the machine running the stack):

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
```

- **Windows:** double-click `caddy-root.crt` → Install Certificate → Local Machine → "Place all certificates in the following store" → Trusted Root Certification Authorities.
- **macOS:** open it in Keychain Access → System keychain → set "When using this certificate" to Always Trust.
- **iPhone:**
  1. AirDrop or email `caddy-root.crt` to the phone and open it → "Profile Downloaded".
  2. Settings → General → VPN & Device Management → install the profile.
  3. Settings → General → About → Certificate Trust Settings → enable full trust for the Caddy root.

Never tap "continue anyway" on a certificate warning for these sites after this — a warning means something is wrong.

## 6. iPhone network setting

Settings → Wi-Fi → (i) next to the home network → Private Wi-Fi Address → **Fixed**. Then reserve that IP in the router's DHCP settings.

## 7. TimeTagger API token

```bash
docker compose exec -it ingestor lifelog get-token --username <your-username>
```

Paste the printed token into `.env` as `TIMETAGGER_TOKEN`, then recreate the ingestor so it picks up the new value:

```bash
docker compose up -d ingestor
docker compose exec ingestor lifelog sync
```

Expected: `success: N records`.

## 8. Use it

- TimeTagger: `https://tt.lifelog.lan` → log in. On the iPhone, open it in Safari → Share → Add to Home Screen.
- Grafana: `https://dash.lifelog.lan` → log in as `admin` with `GRAFANA_ADMIN_PASSWORD` → Lifelog → This Week.
- Sync button: the "Sync now" link at the top of the dashboard (or `https://sync.lifelog.lan/sync`).

## Router checklist (security)

- No port forwarding to the server; UPnP disabled.
- DHCP reservations for the server, iPhone and laptop.
- IoT devices, TVs and guests on the guest Wi-Fi.
````

- [ ] **Step 2: Commit**

```bash
git add docs/setup.md
git commit -m "docs: add setup guide for M1"
```

---

### Task 16: End-to-end verification (M1 done)

This task is manual and follows `docs/setup.md`. Run it first on the development machine (hosts file, `127.0.0.1`), then on the Fedora server with the iPhone.

- [ ] **Step 1: Full test suite passes**

Run: `uv run --directory ingestor pytest -v`
Expected: all tests pass, including the 2 integration tests.

- [ ] **Step 2: Stack starts clean**

Run: `docker compose down -v && docker compose up -d --build && docker compose ps`
Expected: all five services running; `postgres` and `ingestor` healthy. `docker compose logs ingestor` shows `applied migrations: ['001_schemas.sql', '002_raw.sql', '003_core.sql', '004_ops.sql']`.

⚠️ `down -v` deletes all volumes (TimeTagger data, Postgres, Caddy CA). Only use it on a fresh setup, never once real data exists.

- [ ] **Step 3: HTTPS works without warnings** — open `https://tt.lifelog.lan` and `https://dash.lifelog.lan` in a browser that trusts the Caddy root. Expected: padlock, no warning.

- [ ] **Step 4: Token and first sync** — setup guide §7. Expected: `success: 0 records`.

- [ ] **Step 5: Log an activity** — in TimeTagger, record 30 minutes as `#study #math`. Press "Sync now" on the dashboard.
Expected: redirect back to This Week; "Hours per tag this week" shows `study` and `math` at 0.5 h; "Last sync" shows `success`, `0` minutes ago.

- [ ] **Step 6: Timer path** — start a running timer `#gym` in TimeTagger and do nothing for 3 minutes.
Expected: `gym` appears on the dashboard without pressing Sync, and its hours grow on each refresh.

- [ ] **Step 7: Postgres is not exposed** — from another machine on the LAN:
Run: `nmap -p 5432,443 <server-ip>`
Expected: `443/tcp open`, `5432/tcp closed` or `filtered`.

- [ ] **Step 8: iPhone** — on the Fedora deployment, with router DNS, trusted CA and Fixed Wi-Fi address: add TimeTagger to the Home Screen, log an activity, press Sync on the dashboard in Safari.
Expected: the activity appears. **M1 done.**

- [ ] **Step 9: Record completion**

```bash
git commit --allow-empty -m "chore: M1 walking skeleton verified end to end"
```
