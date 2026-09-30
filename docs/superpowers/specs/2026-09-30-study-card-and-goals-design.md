# Study Card and Weekly Goals — Design

**Date:** 2026-09-30
**Status:** Implemented on branch StudyCardandGoals (2026-09-30)
**Branch:** `StudyCardandGoals`
**Builds on:** `2026-09-28-lifelog-sprint1-design.md` (M1 delivered). This is the first slice of M2's feedback loop.

## 1. Purpose

A Garmin-style card for study time: the week's total at a glance and a line that shows whether the week is on pace for the goal. Goals are re-evaluated every week, so they are set per week on a small phone-friendly page with sliders.

## 2. Decisions

| Topic | Decision |
|---|---|
| Card layout | Header like Garmin's weekly card (title with date range, big total, session count) + a line chart: cumulative study hours (solid) against goal pace (dashed) |
| Goals | Set **per week** (no recurring rule). Study goal = sum of subject goals. A week without saved goals shows the actual line only, with "no goal" status |
| Goal entry | `/goals` page served by the ingestor, sliders 0–40 h in 0.5 h steps. Pre-filled with this week's saved goals, or last week's as an unsaved suggestion |
| Subjects | `ds` "DS and Algorithms" (tag `ds` or prefix `ds/`), `systemanalysis` "System Analysis" (tag `systemanalysis`). Stored in `core.subject`; adding one is a migration for now |
| Study rule | An activity counts as study if it has `#study` or any subject tag. It counts **once**, whatever the number of tags |
| Week | ISO week, Monday 00:00 to next Monday 00:00 in `America/Vancouver` (hard-coded until M2's `core.local_tz()`). Activities crossing a boundary count only their in-week part |
| Security | Caddy basic auth on the whole `sync.<domain>` host (covers `/goals` and `/sync`; pulled forward from M4). `POST /goals` requires an `Origin`/`Referer` whose host equals the request host (CSRF protection) |

## 3. Data

Migration `005_study_goals.sql`:

```sql
core.subject (subject text PK, label text, tags text[], tag_prefix text NULL, sort_order smallint)
core.goal    (week_start date, subject text → core.subject, target_hours numeric(4,1) 0..40,
              updated_at timestamptz, PK (week_start, subject), CHECK week_start is a Monday)
```

Seed rows: `('ds', 'DS and Algorithms', '{ds}', 'ds/', 1)`, `('systemanalysis', 'System Analysis', '{systemanalysis}', NULL, 2)`.

## 4. Mart views (Grafana reads only these)

| View | Content |
|---|---|
| `mart.v_current_week` | `week_start` (date), `t0`, `t1` (timestamptz bounds of the current local week) |
| `mart.v_study_activity` | One row per non-deleted study activity: `activity_id`, `started_at`, `ended_at` (now() if running), `subjects` (text[]) |
| `mart.v_study_week` | Current week: `hours`, `sessions`, `goal_hours`, `expected_hours` (goal × elapsed fraction), `status` (`no goal` / `behind` / `ahead` / `done`), `week_label`, `summary` text. Hours count only time up to now (future-dated entries count once they happen). A goal whose subjects are all 0 counts as no goal. |
| `mart.v_study_week_cumulative` | Hourly points from `t0` to `t1` plus `now()`: `actual_hours` (NULL after now), `pace_hours` (NULL when no goal) |
| `mart.v_study_subject_week` | Per subject: `hours`, `goal_hours`, `display` (`DS and Algorithms · 5.5 / 8 h`) |

## 5. Goals page

- `GET /goals`: HTML page (no framework, inline CSS, works on iPhone and in dark mode). Shows the week range, one slider per subject with live total, "Last week: planned X h, did Y h", and whether the values are saved or a suggestion.
- `POST /goals` (form-encoded, parsed with the standard library): validates week is the current or a future Monday, known subjects, 0 ≤ value ≤ 40, multiples of 0.5; upserts in one transaction; `303` to the dashboard. Errors: `400` with a readable message; missing or foreign `Origin`/`Referer`: `403`.
- Logic lives in `lifelog/goals.py` (database access, validation) and `lifelog/goals_page.py` (HTML rendering). `app.py` only wires HTTP.

## 6. Grafana

This Week dashboard gets a **Study** section on top: total stat (`5.8 h`), summary stat (`Sep 28 – Oct 4 · 2 sessions · goal 12 h · ahead`), line chart (Study solid, Goal pace dashed), subject rows. Dashboard links: **Sync now**, **Set goals**. Dashboard time range becomes the current week (`now/w`), week starting Monday. Existing panels (Last sync, Hours per tag) move below.

## 7. Security and configuration

- New `.env` values: `SYNC_BASIC_AUTH_USER`, `SYNC_BASIC_AUTH_HASH` (bcrypt from `caddy hash-password`, single-quoted). Required by `compose.yaml`.
- The ingestor itself has no authentication; it is only reachable through Caddy.

## 8. Testing

- Views: counted once with several tags; subject tag without `#study`; `ds/` prefix vs. unrelated `dsx`; non-study and deleted excluded; week-boundary clamping; no-goal week; pace reaches the goal at week end; subject rows; `grafana_ro` can read the new views.
- Goals: save/load round trip, upsert, validation errors, current week from the database, last week planned/done.
- HTTP: GET renders saved values and suggestions; POST saves and redirects; bad values → 400; missing/foreign origin → 403.

## 9. Out of scope

Other activities and cards, adding subjects from the page, `goals.yaml`, full taxonomy, streaks, personal records, per-record timezones.
