# Lifelog — Sprint 1 Design

**Date:** 2026-09-28
**Status:** Draft, pending user review
**Project:** Personal activity tracker proof of concept ("Strava / Garmin Connect for all my activities")

---

## 1. Purpose

Build a self-hosted system that makes it low-friction to record personal activities (study, work, sport, rest, ...) and turns that data into a **positive feedback loop**: dashboards that show goals, pace, streaks and personal records, so the user keeps returning to the activities they want to do.

Not tracking an activity is itself the signal. The system does not try to account for every hour of the day; it tracks the activities the user cares about and makes their presence (or absence) visible.

Later sprints add one-tap start/stop from phone and watch, Garmin data, and a small local LLM "coach". Sprint 1 must leave clean seams for those without building them.

## 2. Requirements and constraints

| # | Requirement | How this design meets it |
|---|---|---|
| R1 | Easy start/stop from phone and watch | Sprint 1: TimeTagger PWA on iPhone. Sprint 2: trigger API + iOS Shortcuts / Garmin Connect IQ (seam: pluggable sources, `core.activity` is source-agnostic) |
| R2 | Dashboards live or near-live, with a sync button | Ingestor syncs every 2 min + `/sync` button; Grafana auto-refresh; running timers computed with `now()` |
| R3 | Open source wherever possible | All components open source. Accepted exception: Docker Desktop on Mac/Windows demo machines (Docker Engine on Linux is open source) |
| R4 | Fedora server, NVIDIA RTX 3050 6 GB, Linux-first | Docker Engine on Fedora; LLM (sprint 3) sized for 6 GB VRAM |
| R5 | No private data on servers the user does not control | Fully self-hosted, LAN-only in sprint 1, all telemetry disabled, no port forwarding |
| R6 | Portable: can be demoed on a Mac or Windows machine | Single `compose.yaml`; all config and dashboards in the repo |

User context: lives in Vancouver (`America/Vancouver`), iPhone + Garmin watch, early riser, travels roughly once a year.

## 3. Scope

### In scope (sprint 1)

- TimeTagger as the capture UI (iPhone PWA over HTTPS, manual start/stop)
- Ingestor: incremental sync TimeTagger → PostgreSQL, scheduled + on-demand
- PostgreSQL with `raw`, `core`, `mart`, `ops` schemas
- Faceted tag taxonomy and weekly goals as YAML in the repo
- Data contract and data-quality checks
- Grafana dashboards: This Week, Trends, Data Health
- Caddy reverse proxy with local HTTPS, basic auth, IP allowlist
- Nightly backups with one tested restore
- Automated tests

### Out of scope (later sprints)

| Item | Target sprint |
|---|---|
| Trigger API (`/start`, `/stop`), iOS Shortcuts, NFC tags, Action Button | 2 |
| Garmin Connect IQ widget, Garmin data ingestion (workouts, sleep, HR) | 2 |
| ntfy push nudges for forgotten timers | 2 |
| Per-record timezone capture, travel log | 2+ |
| Local LLM coach (Ollama, `compose.gpu.yaml`) | 3 |
| Remote access (WireGuard / Headscale / Tailscale — a server-wide decision) | separate |
| dbt-core (adopt when mart grows beyond ~15 views) | later |
| Custom mobile "This Week" page or Evidence.dev showcase | later |

## 4. Architecture

```
 iPhone (PWA) ──HTTPS──►┌───────────── Caddy :443 (only exposed port) ─────────────┐
 Laptop browser         │ tt.lifelog.lan   dash.lifelog.lan   sync.lifelog.lan      │
                        └──────┬──────────────────┬──────────────────┬──────────────┘
                               ▼                  ▼                  ▼
                        ┌────────────┐     ┌────────────┐     ┌──────────────┐
                        │ TimeTagger │     │  Grafana   │     │   Ingestor   │
                        │  (SQLite)  │     │ (read-only)│     │ timer + /sync│
                        └─────┬──────┘     └─────▲──────┘     └──────┬───────┘
                              │ /api/v2/updates   │                   │
                              └───────────────────┼──────────────────►│
                                                  │                   ▼
                        ┌─────────────────────────┴──── PostgreSQL ─────────────┐
                        │ raw → core → mart            ops (sync, dq results)   │
                        └───────────────────────────────────────────────────────┘
                        ┌──────────┐
                        │  backup  │ nightly pg_dump + TimeTagger SQLite copy
                        └──────────┘
```

Seams for later sprints:
- New data sources (trigger API, Garmin) implement the ingestor's source interface and write `core.activity`.
- New consumers (LLM coach, Evidence.dev, custom page) read `mart` views only.

## 5. Services and deployment

Runtime: Docker Engine + Docker Compose on Fedora. The same `compose.yaml` runs on macOS/Windows (Docker Desktop, Colima, Rancher Desktop or Podman Desktop).

| Service | Image | Role | Network exposure |
|---|---|---|---|
| `postgres` | `postgres:17` | Database `lifelog` | Internal network only — no `ports:` |
| `timetagger` | `ghcr.io/almarklein/timetagger` | Capture UI + API; SQLite on a named volume; credentials from `.env` | Via Caddy only |
| `ingestor` | Built from `ingestor/` | Sync pipeline, config loader, DQ checks, `/sync`, `/health` | Via Caddy only |
| `grafana` | `grafana/grafana-oss` | Dashboards, provisioned from files | Via Caddy only |
| `caddy` | `caddy` | Reverse proxy, local TLS, auth, allowlist | `443` on the LAN |
| `backup` | Small image with `pg_dump` + `sqlite3` + cron | Nightly backups | None |

Services that need a host port for debugging bind to `127.0.0.1` only, never `0.0.0.0` (Docker-published ports on `0.0.0.0` bypass firewalld).

### Repository layout

```
TimeTracker/
├── compose.yaml
├── .env.example                  # committed; real .env is git-ignored
├── caddy/Caddyfile
├── config/
│   ├── taxonomy.yaml             # tag tree, facets, colors, max durations, mood scores
│   └── goals.yaml                # weekly goals with effective dates
├── contracts/activity.yaml       # data contract
├── db/
│   ├── migrations/               # 001_schemas.sql, 002_raw.sql, 003_core.sql, 004_ops.sql ... (applied once, in order)
│   ├── views/                    # mart views, CREATE OR REPLACE, re-applied on every start
│   └── checks/                   # dq_*.sql (data issues) and test_*.sql (integrity assertions)
├── grafana/
│   ├── provisioning/datasources/lifelog.yaml
│   ├── provisioning/dashboards/lifelog.yaml
│   └── dashboards/               # this-week.json, trends.json, data-health.json
├── ingestor/                     # Python package, Dockerfile, tests/
├── backup/                       # Dockerfile + backup script
└── docs/
```

### Caddy and network security

- **TLS:** `tls internal`. Caddy runs its own local certificate authority. Its root certificate is installed once on the iPhone (Settings → General → About → Certificate Trust Settings) and the laptop. HTTPS is needed so the TimeTagger PWA can use a service worker on iOS (offline capture).
- **Hostnames:** `tt.lifelog.lan` (TimeTagger), `dash.lifelog.lan` (Grafana), `sync.lifelog.lan` (ingestor). They need a local DNS entry pointing at the server. iOS has no editable hosts file, so this must come from the router's local DNS, or a self-hosted DNS server (AdGuard Home) if the router cannot do it. See §14.
- **Basic auth** on `sync.lifelog.lan` (the ingestor has no login of its own).
- **IP allowlist:** all sites accept only the IPs listed in `ALLOWED_CLIENT_IPS` (the user's iPhone and laptop, with DHCP reservations on the router).
- **Application logins:** strong unique passwords for TimeTagger and Grafana; Grafana anonymous access disabled.
- **Telemetry disabled:** `GF_ANALYTICS_REPORTING_ENABLED=false`, `GF_ANALYTICS_CHECK_FOR_UPDATES=false`, `GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false`.

Threat model summary:

| Threat | Mitigation |
|---|---|
| Sniffing on home Wi-Fi | HTTPS (Caddy) |
| Impersonation / man-in-the-middle | Trusted local root CA + never bypass certificate warnings |
| Other LAN devices accessing the apps | Allowlist + basic auth + app passwords + untrusted devices on guest Wi-Fi |
| Internet reaching in | Router: no port forwarding to the server, UPnP disabled |

User-side checklist (router and devices, not automated): no port forwarding and UPnP off; DHCP reservations for server, iPhone, laptop; IoT/TV/guests on guest Wi-Fi; install Caddy root CA on devices; never bypass certificate warnings.

## 6. Data model

### 6.1 Layers

| Schema | Purpose | Shape | Written by | Read by |
|---|---|---|---|---|
| `raw` | Exact copy of source payloads; enables replay and debugging | As received (JSONB) | Ingestor | Ingestor (rebuild) |
| `core` | Integrated, source-agnostic, single version of truth | Normalized (3NF) | Ingestor | Mart views |
| `mart` | Consumer-shaped views; the **public interface** | Star schema + bridge | You (SQL views) | Grafana, later LLM |
| `ops` | Operational metadata: sync state, runs, DQ results | Tables | Ingestor | Grafana (Data Health) |

Database roles:
- `ingestor`: read/write `raw`, `core`, `ops`; owns views.
- `grafana_ro`: `SELECT` on `mart` and `ops` only. No access to `raw` or `core`.

### 6.2 raw

```sql
raw.timetagger_record (
  key         text PRIMARY KEY,          -- TimeTagger record key
  payload     jsonb NOT NULL,            -- full record as received
  server_ts   double precision NOT NULL, -- TimeTagger "st"; upsert only if newer
  fetched_at  timestamptz NOT NULL DEFAULT now()
)
```

Never edited by hand, never interpreted. Upserts only replace a row when the incoming `server_ts` is newer.

### 6.3 core

```sql
core.activity (
  activity_id        bigserial PRIMARY KEY,
  source             text NOT NULL,          -- 'timetagger' (later: 'trigger', 'garmin')
  source_id          text NOT NULL,
  started_at         timestamptz NOT NULL,   -- UTC
  ended_at           timestamptz,            -- NULL = running
  description        text,                   -- sensitivity: private (see contract)
  is_deleted         boolean NOT NULL DEFAULT false,
  source_updated_at  timestamptz,
  ingested_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source, source_id),
  CHECK (ended_at IS NULL OR ended_at >= started_at)
)

core.activity_tag (
  activity_id  bigint REFERENCES core.activity ON DELETE CASCADE,
  tag          text NOT NULL,       -- lowercased, without '#'
  position     smallint NOT NULL,   -- order in the description, 1-based
  PRIMARY KEY (activity_id, tag)
)

core.tag (
  tag           text PRIMARY KEY,
  parent_tag    text REFERENCES core.tag,
  facet         text NOT NULL CHECK (facet IN ('activity','quality','place','mood','unmapped')),
  color         text,
  max_duration  interval,          -- forgotten-timer threshold (activity facet)
  score         smallint           -- mood facet only: 1 (awful) .. 5 (great)
)

core.goal (
  goal_id       bigserial PRIMARY KEY,
  tag           text NOT NULL REFERENCES core.tag,
  period        text NOT NULL CHECK (period = 'week'),
  target_hours  numeric,
  target_count  integer,
  min_minutes   integer,           -- a session counts toward target_count only if >= this
  valid_from    date NOT NULL,
  valid_to      date,              -- derived: next valid_from for the same tag, else NULL
  CHECK ((target_hours IS NULL) <> (target_count IS NULL))
)
```

`core.activity_tag` is a junction table (not an array column) so that `core` stays in first normal form.

Tags seen in data but missing from `taxonomy.yaml` are inserted into `core.tag` with `facet = 'unmapped'` and surface on the Data Health dashboard.

### 6.4 Faceted taxonomy (`config/taxonomy.yaml`)

Tags answer four independent questions. Each is a separate root ("facet"):

| Facet | Question | Cardinality per activity | Examples |
|---|---|---|---|
| `activity` | What did I do? | **exactly 1** | study → math, physics; work → coding, meetings; sport → gym, run; rest → sleep |
| `quality` | How did it go? | 0–1 | deepwork, shallow, distracted |
| `place` | Where? | 0–1 | home, library, office |
| `mood` | How did I feel? | 0–1 | great (5), good (4), meh (3), low (2), awful (1) |

Example:

```yaml
activity:
  study:
    color: "#3b82f6"
    max_duration: 5h
    children:
      math: {}
      physics: {}
      reading: {}
  sport:
    children:
      gym: { max_duration: 3h }
      run: { max_duration: 3h }
  rest:
    children:
      sleep: { max_duration: 12h }
quality:
  deepwork: {}
  shallow: {}
  distracted: {}
place:
  home: {}
  library: {}
mood:
  great: { score: 5 }
  good:  { score: 4 }
  meh:   { score: 3 }
  low:   { score: 2 }
  awful: { score: 1 }
```

`max_duration` is inherited from the nearest ancestor that defines it. Re-parenting a tag changes history for all past data (deliberate SCD type 1 behavior).

### 6.5 Goals (`config/goals.yaml`)

```yaml
- tag: study    period: week  target_hours: 10  from: 2026-10-01
- tag: gym      period: week  target_count: 3   from: 2026-10-01
- tag: reading  period: week  target_count: 4   min_minutes: 20  from: 2026-10-01
```

- A goal on a tag includes all descendant tags (a goal on `study` counts `math` and `physics`).
- Goals are versioned by effective date (SCD type 2): changing a target adds a new entry with a new `from`, so past weeks are judged against the target that applied then.
- Weeks start Monday (ISO 8601).

### 6.6 Time handling

- All timestamps stored as `timestamptz` in UTC.
- Local days are cut at **midnight** in `HOME_TZ` (`America/Vancouver`), which handles daylight saving automatically via Postgres's built-in IANA tz database.
- All mart SQL calls `core.local_tz(activity_id)` instead of hard-coding a timezone. In sprint 1 it returns the `HOME_TZ` setting (stored in `ops.setting`). Later it will prefer a per-record timezone and then a travel-log entry, without changing any mart view.
- Running activities (`ended_at IS NULL`) use `now()` as their end in mart views, so durations tick live.

### 6.7 mart (star schema with bridge)

| View | Grain / purpose |
|---|---|
| `mart.dim_date` | One row per local date: weekday, ISO week, month, is_weekend |
| `mart.dim_tag` | One row per tag, hierarchy flattened: `facet`, `level1`, `level2`, `color`, `score` |
| `mart.fact_segment` | **One activity on one local day.** Activities crossing local midnight are split. Columns: `segment_id`, `activity_id`, `local_day`, `segment_start`, `segment_end`, `hours`, `is_running` |
| `mart.bridge_segment_tag` | One row per (segment, tag). Per facet: `is_primary` = first tag of that facet by position; `weight` = 1 / number of tags of that facet on the activity |
| `mart.v_daily_category_hours` | Hours per day per activity-facet category using the primary rule. Sums to real tracked time |
| `mart.v_daily_tag_exposure` | Hours per day per tag with no allocation. Non-additive across tags; named "exposure" to avoid misuse in totals |
| `mart.v_goal_progress` | Current week per goal: actual, expected-by-now, status |
| `mart.v_goal_history` | Past weeks per goal: target in effect, actual, met yes/no |
| `mart.v_streaks` | Consecutive completed weeks meeting each goal. The in-progress week never breaks a streak |
| `mart.v_days_since_last` | Per goal tag: days since the last activity |
| `mart.v_personal_records` | Longest single session per activity tag; most hours in one week per goal tag; flag if set this week |
| `mart.v_running` | Currently running activities with elapsed time |
| `mart.v_sync_status` | Last sync time, status, error |
| `mart.v_dq_issues` | Open data-quality issues with details |

Allocation rules, all from the one bridge:

| Rule | Query | Additive across tags |
|---|---|---|
| First tag (primary) | `SUM(hours) WHERE is_primary` | Yes |
| Split | `SUM(hours * weight)` | Yes |
| Exposure | `SUM(hours)` | No |

Pace status in `v_goal_progress`:

```
expected = target × (elapsed time in week / week length)
Done          actual >= target
On track      actual >= expected
Slightly behind  actual >= 0.75 × expected
Behind        otherwise — shown as remaining amount per remaining day, not as failure
```

Views are plain (not materialized). At personal data volume they run in milliseconds and are always live.

### 6.8 Data-quality checks (`db/checks/dq_*.sql`)

Each check is a query returning offending rows. Results go to `ops.dq_result`. **Flag, never auto-fix.** The user corrects the data in TimeTagger and the fix syncs through.

| Check | Rule |
|---|---|
| `dq_too_short` | Completed activity shorter than 5 minutes |
| `dq_over_max_duration` | Running or completed activity longer than its tag's `max_duration` (forgotten timer) |
| `dq_activity_facet` | Activity with 0 or more than 1 `activity`-facet tag |
| `dq_other_facets` | More than 1 tag in `quality`, `place` or `mood` |
| `dq_overlap` | Two non-deleted activities overlapping by more than 1 minute |
| `dq_unmapped_tag` | Tag with facet `unmapped` |
| `dq_parse_error` | Raw record that could not be transformed into core |

### 6.9 Integrity assertions (`db/checks/test_*.sql`)

Queries that must return zero rows. A failure means a bug in the pipeline or views, not a data issue. Run after every sync and in the test suite.

- Every segment's bridge weights sum to 1 within each facet that has tags.
- Every segment with at least one tag of a facet has exactly one primary tag for that facet.
- No segment has negative or zero-length duration (except running activities at the instant of start).
- Sum of segment hours per activity equals the activity's duration.

### 6.10 ops

```sql
ops.sync_state (source text PRIMARY KEY, cursor text, last_success_at timestamptz, last_error text)
ops.sync_run   (run_id bigserial PRIMARY KEY, source text, trigger text, started_at timestamptz,
                finished_at timestamptz, n_records integer, status text, error text)
ops.dq_result  (run_id bigint, check_name text, activity_id bigint, detail jsonb)
ops.setting    (key text PRIMARY KEY, value text)            -- e.g. home_tz
```

## 7. Data contract (`contracts/activity.yaml`)

The contract is the agreement between producers (TimeTagger, ingestor, future sources) and consumers (Grafana, future LLM). It is enforced by the DQ checks and integrity assertions.

| Topic | Rule |
|---|---|
| Public interface | `mart.*` views are the contract. `core` is internal; `raw` is private to the ingestor |
| Change policy | Adding columns: allowed anytime. Renaming/removing columns or changing meaning: new view version (`_v2`); old version kept until no consumer uses it |
| Identity | `(source, source_id)` is stable and unique; all loads are idempotent |
| Lineage | Every core row carries `source`, `source_updated_at`, `ingested_at` |
| Time | UTC storage; local day = midnight in `HOME_TZ`; ISO weeks (Monday) |
| Tagging | Facet cardinalities per §6.4 |
| Validity | Minimum 5 minutes; maximum per tag `max_duration`; no overlaps — all flagged, not fixed |
| Freshness | Data at most 2 minutes old under normal operation; staleness shown on dashboards |
| Sensitivity | `description` = private. Tags, durations, aggregates = internal. The future LLM receives aggregates and tags only, never descriptions, unless explicitly enabled |
| Retention | Keep forever. Deletes are soft (`is_deleted`) |
| Untracked time | Not modeled. Absence of tracking for a goal activity is surfaced through goals, streaks and days-since-last |

## 8. Ingestion and sync

### 8.1 Sync run

Triggered by the in-process timer (every `SYNC_INTERVAL_SECONDS`, default 120) or by `GET /sync`.

1. Acquire a Postgres advisory lock. If a sync is already running: the timer skips this round; the button waits for it to finish.
2. Open **one transaction**:
   1. Read cursor from `ops.sync_state`.
   2. `GET <TimeTagger>/api/v2/updates?since=<cursor>`.
   3. Upsert changed records into `raw.timetagger_record` (only if `server_ts` is newer).
   4. Transform changed records → upsert `core.activity`, replace that activity's `core.activity_tag` rows, insert unseen tags as `unmapped`.
   5. Save the new cursor.
3. Commit.
4. Run DQ checks and integrity assertions → `ops.dq_result`. Failures never block data.
5. Record the run in `ops.sync_run`.

The cursor is saved in the same transaction as the data, so a crash at any point leaves the database unchanged and the next run re-fetches the same changes. Combined with idempotent upserts, every sync is safe to repeat.

### 8.2 Nightly reconciliation

Once per night the ingestor performs a full fetch (cursor 0) and marks core activities whose source record no longer exists as deleted. This catches anything the incremental sync could miss.

### 8.3 Config loading

On startup and at the beginning of each sync, the ingestor loads `config/taxonomy.yaml` and `config/goals.yaml`, validates them (pydantic), and replaces `core.tag` / `core.goal` in a transaction. On validation error the previous version stays active and the error is shown on the status tile.

### 8.4 Startup

1. Wait for Postgres.
2. Apply `db/migrations/*.sql` not yet recorded in `ops.schema_migration`, in order.
3. Re-apply all `db/views/*.sql` (`CREATE OR REPLACE VIEW`).
4. Load config, run an initial sync, start the timer and HTTP server.

### 8.5 Modules

| Module | Responsibility |
|---|---|
| `sources/base.py` | Source interface: `fetch(cursor) -> (records, new_cursor, reset)` and `to_core(record) -> ActivityIn` |
| `sources/timetagger.py` | API client; parses `#tags` from the description, the deletion marker, running state (`t1 == t2`) |
| `config_loader.py` | Loads and validates taxonomy and goals |
| `pipeline.py` | The sync transaction |
| `dq.py` | Runs `db/checks/*.sql` |
| `db.py` | Connection, migrations, views |
| `app.py` | FastAPI app: `GET /sync` (sync then redirect to `DASHBOARD_URL`), `GET /health`, background timer |

Stack: Python 3.12, `httpx`, `psycopg` 3, `pydantic`, `fastapi` + `uvicorn`, `pyyaml`, managed with `uv`. Structured JSON logs to stdout.

### 8.6 Error handling

| Failure | Behavior |
|---|---|
| TimeTagger unreachable / timeout | 3 retries with exponential backoff; run marked failed; cursor unchanged; status tile shows time since last success |
| HTTP 401 / 403 | No retry; run marked failed with an explicit auth error |
| Single record fails to parse | Kept in raw, skipped in core, `dq_parse_error` recorded; rest of batch continues |
| `reset: true` from TimeTagger | Full resync from cursor 0 |
| Invalid taxonomy/goals YAML | Previous version kept; error on status tile |
| Postgres unavailable | Container healthcheck + `restart: unless-stopped`; sync resumes automatically |
| Concurrent button + timer | Advisory lock; no duplicate work |

### 8.7 Configuration (`.env`)

```bash
POSTGRES_PASSWORD=...
INGESTOR_DB_PASSWORD=...
GRAFANA_RO_DB_PASSWORD=...
TIMETAGGER_CREDENTIALS=...          # user:bcrypt-hash
TIMETAGGER_URL=http://timetagger:80
TIMETAGGER_TOKEN=...                # API token from TimeTagger account page
SYNC_INTERVAL_SECONDS=120
HOME_TZ=America/Vancouver
DASHBOARD_URL=https://dash.lifelog.lan/d/this-week
GRAFANA_ADMIN_PASSWORD=...
SYNC_BASIC_AUTH_USER=...
SYNC_BASIC_AUTH_HASH=...
ALLOWED_CLIENT_IPS=192.168.1.20 192.168.1.21
BACKUP_DIR=/mnt/backup/lifelog
```

## 9. Dashboards (Grafana)

All dashboards and the Postgres datasource (user `grafana_ro`) are provisioned from `grafana/`. Changes are made in the Grafana UI, exported as JSON, and committed. Auto-refresh: 1 minute.

### 9.1 This Week (home; single column, readable on iPhone)

| Panel | Content |
|---|---|
| Status | Last sync ("1 min ago ✅"), **Sync** link to `https://sync.lifelog.lan/sync`, open DQ issue count linking to Data Health |
| Now | Running timer, if any ("Study · 47 min") |
| Goals | One bar gauge per goal with threshold colors for pace status; "X h to go, ~Y min/day" when behind |
| Streaks | Consecutive weeks each goal was met |
| Days since last | Per goal tag |
| New records | Personal records set this week |

### 9.2 Trends

- Weekly hours per activity category, last 12 weeks, with 8-week baseline
- Share of study time tagged `deepwork`
- Mood score trend
- Weekday × hour heatmap of activity time

### 9.3 Data Health

- Open DQ issues table (check, activity, detail)
- Unmapped tags
- Sync run history (status, duration, record counts)

## 10. Operations

### Backups

TimeTagger's SQLite database is the only source of truth for manually captured activities; Postgres can be rebuilt from it, not the other way round. The `backup` service runs nightly at 03:00 local time:

- `pg_dump` of `lifelog`
- SQLite online backup (`sqlite3 .backup`) of the TimeTagger database
- Writes to `BACKUP_DIR`, which must be on a different disk than the Docker volumes
- Keeps 30 days of backups

Grafana is not backed up: its dashboards and datasource live in the repo; only the admin password (in `.env`) is needed to recreate it.

A restore procedure is documented in `docs/restore.md` and executed once as part of sprint 1.

## 11. Testing

Test-first with `pytest`. Run everything with `just test`.

| Level | Covers | Runs against |
|---|---|---|
| Unit | Tag parsing, facet assignment, deletion marker, running detection, YAML validation, max_duration inheritance | No database |
| SQL / mart | Midnight split; DST transition days (23 h and 25 h days in Vancouver); bridge weights and primary flags; goal effective dates; pace status; streaks; days since last; personal records | Real Postgres via `testcontainers`, fixture data |
| DQ checks | Each `dq_*.sql` triggered by a fixture designed to fail it; clean fixture triggers none | Same |
| Integrity assertions | Each `test_*.sql` returns zero rows on fixture data | Same |
| Pipeline | Idempotency (sync twice → identical state); crash safety (injected failure after raw upsert → nothing committed, cursor unchanged); parse-error isolation; reset handling | Same, with a fake source |
| Integration | Real TimeTagger container: create records through its API → sync → assert `core` and `mart` | Compose test profile |

## 12. Definition of done

1. `docker compose up` on the Fedora server starts the full stack; the same repo also starts once on the Mac.
2. TimeTagger PWA installed on the iPhone over HTTPS with a trusted certificate; an activity can be started and stopped.
3. The activity appears on This Week within 3 minutes, or immediately after pressing Sync.
4. Goals show correct pace status; streaks, days-since-last and personal records are correct for fixture and real data.
5. A deliberately bad record (a 2-minute entry, an unmapped tag) appears on Data Health.
6. `nmap` from the laptop shows only 443 (and SSH) open on the server; Postgres is not reachable from the LAN.
7. Grafana telemetry and update checks are disabled.
8. A nightly backup has been produced and successfully restored once.
9. All tests pass.

## 13. Future sprints — design notes

- **Trigger API (sprint 2):** `POST /start/{tag}`, `POST /stop`; enforces one running timer (starting a new one stops the current one); records the client's timezone per activity. Callers: iOS Shortcuts (home-screen, Action Button, NFC automations), Garmin Connect IQ widget via `makeWebRequest`.
- **Nudges (sprint 2):** ingestor sends ntfy notifications for timers past `max_duration`. On iOS, self-hosted ntfy relays only a wake-up signal through Apple, never message content.
- **Garmin (sprint 2):** a new ingestor source; sport activities into `core.activity`, continuous metrics (sleep, HR) into a future `core.metric` table.
- **Timezones (sprint 2+):** `core.local_tz()` prefers per-record timezone, then a `core.tz_period` travel log, then `HOME_TZ`.
- **LLM coach (sprint 3):** Ollama with a 3–4B model at Q4 on the RTX 3050 (`compose.gpu.yaml`, Linux only; native Ollama on Mac/Windows via `LLM_BASE_URL`). Input is a compact markdown "context pack" rendered from mart views (aggregates and tags only). Roles: explain trends, help plan study, propose correlation hypotheses that the user verifies in SQL (e.g. with `corr()` on a daily matrix view). Business glossary in `docs/` provides definitions as context.

## 14. To verify during implementation

- TimeTagger self-hosted API base path (expected `/timetagger/api/v2/...`), auth header name, `updates` response fields, and how deleted/hidden records are marked.
- TimeTagger PWA offline behavior on iOS Safari.
- `TIMETAGGER_CREDENTIALS` format for the Docker image.
- Whether the user's router supports local DNS entries; if not, add AdGuard Home as a local DNS server.
- Grafana bar gauge suitability for pace colors; fallback to stat panels.

## 15. Decision log

| Decision | Chosen | Alternatives | Rationale |
|---|---|---|---|
| Capture tool | TimeTagger | solidtime, Kimai, Traggo | Free-form tags fit life tracking; lightweight; incremental `updates` API. solidtime is client/project/billing oriented |
| Container runtime | Docker | Podman + Quadlets | Portability to Mac/Windows for demos. Firewall bypass handled by never publishing on `0.0.0.0` |
| Dashboards | Grafana | Metabase, Evidence.dev, Superset | Live refresh, dashboards as code in the repo, threshold colors, light footprint; single SQL-literate author |
| Architecture layers | raw → core → mart (+ ops) | Single schema | Each boundary absorbs one kind of change: source changes, meaning, consumer needs |
| Core shape | Normalized with junction table | Array column | First normal form; clean joins |
| Multi-tag allocation | Star schema with bridge (primary, weight) | One mart per rule | One place for shared logic; rules become columns |
| Tag model | Faceted taxonomy (activity, quality, place, mood) | Flat tags | Separates "what" from "how/where/feeling"; removes most double counting |
| Time | UTC storage, midnight in `HOME_TZ` via `core.local_tz()` | Pure UTC days | UTC midnight is 16:00/17:00 in Vancouver; function leaves room for travel |
| Goals | Sprint 1, weekly, effective-dated | Sprint 3 | Goals are the core of the feedback loop |
| Untracked time | Not modeled | Coverage % | Absence of tracking is the signal; surfaced via goals, streaks, days since last |
| Data quality | Flag, never fix | Auto-correct | Keeps user in control; mistakes stay visible |
| Views | Plain views | Materialized views, dbt | Tiny data volume; always live; dbt when the mart grows |
