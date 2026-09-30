-- Bounds of the current local week: Monday 00:00 to next Monday 00:00 in America/Vancouver.
-- The timezone is hard-coded until M2's core.local_tz().
-- Note: tzdata records British Columbia's move to permanent daylight time from Nov 2026, so Vancouver weeks no longer change length; the bounds still handle DST correctly for any zone.
CREATE VIEW mart.v_current_week AS
SELECT local_monday::date AS week_start,
       local_monday AT TIME ZONE 'America/Vancouver' AS t0,
       (local_monday + interval '7 days') AT TIME ZONE 'America/Vancouver' AS t1
FROM (SELECT date_trunc('week', now() AT TIME ZONE 'America/Vancouver') AS local_monday) m;
