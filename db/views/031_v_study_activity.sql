-- Activities that count as study: tagged #study or with a subject tag (see core.subject).
-- One row per activity, however many tags it has. Running activities end at now().
CREATE VIEW mart.v_study_activity AS
SELECT a.activity_id,
       a.started_at,
       coalesce(a.ended_at, now()) AS ended_at,
       coalesce(array_agg(DISTINCT s.subject) FILTER (WHERE s.subject IS NOT NULL), '{}') AS subjects
FROM core.activity a
JOIN core.activity_tag t USING (activity_id)
LEFT JOIN core.subject s
       ON t.tag = ANY (s.tags)
       OR (s.tag_prefix IS NOT NULL AND starts_with(t.tag, s.tag_prefix))
WHERE NOT a.is_deleted
GROUP BY a.activity_id, a.started_at, a.ended_at
HAVING bool_or(t.tag = 'study') OR count(s.subject) > 0;
