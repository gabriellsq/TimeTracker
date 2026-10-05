# Forgotten Timer Guard — Design (C1: detection)

**Date:** 2026-10-05
**Status:** Approved in conversation
**Builds on:** Study card and weekly goals (`2026-09-30-study-card-and-goals-design.md`). C2 (iPhone nudges via ntfy) follows after the Fedora deployment.

## 1. Purpose

A timer left running inflated the Study card to 23 h. Make suspiciously long entries visible at the top of the dashboard so they get fixed in TimeTagger quickly. Flag, never auto-correct (sprint 1 spec §6.8).

## 2. Rules

| Rule | Decision |
|---|---|
| Limit | **6 hours**, one limit for all activities (per-activity limits come with M3's taxonomy) |
| Running timer | Flagged when running for more than 6 h |
| Finished entry | Flagged when longer than 6 h **and** it ended within the last 7 days (older ones age out) |
| Opt-out | An entry tagged `#long` is never flagged (for genuinely long sessions) |
| Ignored | Deleted entries |

## 3. Data

View `mart.v_long_timers` (one row per flagged activity): `activity_id`, `started_at`, `ended_at`, `running`, `hours`, `tags` (`#a #b`, or `(no tags)`), `display`:

- Running: `⚠️ Running 7 h 12 m · #study #personalproject — forgot to stop?`
- Finished: `⚠️ 23.2 h on Wed Sep 30 · #study #personalproject — fix in TimeTagger or tag #long`

Dates in `America/Vancouver` (hard-coded until M2's `core.local_tz()`).

## 4. Dashboard

A "Timer check" stat tile at the very top of This Week, one line per flagged entry; when none: `✅ No long timers`. Other panels move down.

## 5. Testing

View tests: running over/under the limit, finished over the limit recently, finished long ago, exactly-under limit, `#long` opt-out, deleted, untagged, display texts, `grafana_ro` access. The existing dashboard test verifies the tile's query and field regex.

## 6. Out of scope

Push notifications (C2), per-activity limits (M3), auto-stopping timers (sprint 2 trigger API stops the previous timer by design).
