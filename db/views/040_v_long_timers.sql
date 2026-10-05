-- Suspiciously long timers ("forgot to stop?"):
--   running for more than 6 hours, or
--   finished within the last 7 days after more than 6 hours.
-- Tag an entry #long in TimeTagger to mark it as intentional.
-- The 6-hour limit lives here until M3's per-activity limits; dates use America/Vancouver until M2.
CREATE VIEW mart.v_long_timers AS
SELECT f.*,
       CASE WHEN f.running
            THEN '⚠️ Running ' || floor(f.hours)::int || ' h '
                 || lpad(floor((f.hours - floor(f.hours)) * 60)::int::text, 2, '0') || ' m · '
                 || f.tags || ' — forgot to stop?'
            ELSE '⚠️ ' || to_char(round(f.hours, 1), 'FM990.0') || ' h on '
                 || to_char(f.started_at AT TIME ZONE 'America/Vancouver', 'Dy Mon FMDD') || ' · '
                 || f.tags || ' — fix in TimeTagger or tag #long'
       END AS display
FROM (
    SELECT a.activity_id,
           a.started_at,
           a.ended_at,
           a.ended_at IS NULL AS running,
           extract(epoch FROM coalesce(a.ended_at, now()) - a.started_at) / 3600 AS hours,
           coalesce(string_agg('#' || t.tag, ' ' ORDER BY t.position), '(no tags)') AS tags
    FROM core.activity a
    LEFT JOIN core.activity_tag t USING (activity_id)
    WHERE NOT a.is_deleted
      AND coalesce(a.ended_at, now()) - a.started_at > interval '6 hours'
      AND (a.ended_at IS NULL OR a.ended_at > now() - interval '7 days')
    GROUP BY a.activity_id, a.started_at, a.ended_at
    HAVING NOT coalesce(bool_or(t.tag = 'long'), false)
) f;
