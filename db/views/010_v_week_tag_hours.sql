-- PROVISIONAL (M1): hours per tag in the current local ISO week.
-- No midnight split, timezone hard-coded. Replaced by the star schema in M2.
-- Activities without tags are shown as '(untagged)'.
CREATE VIEW mart.v_week_tag_hours AS
SELECT
    coalesce(t.tag, '(untagged)') AS tag,
    round(
        sum(extract(epoch FROM coalesce(a.ended_at, now()) - a.started_at))::numeric / 3600,
        2
    ) AS hours
FROM core.activity a
LEFT JOIN core.activity_tag t USING (activity_id)
WHERE NOT a.is_deleted
  AND a.started_at >= date_trunc('week', now() AT TIME ZONE 'America/Vancouver') AT TIME ZONE 'America/Vancouver'
GROUP BY 1;
