-- Study activities in the current week, with the hours done so far inside it:
-- clamped to the week bounds and to now(), so future-dated entries only count once they happen.
-- Zero-length entries are dropped (they are not sessions).
CREATE VIEW mart.v_study_week_activity AS
SELECT *
FROM (
    SELECT sa.activity_id,
           sa.started_at,
           sa.ended_at,
           sa.subjects,
           extract(epoch FROM least(sa.ended_at, w.t1, now()) - greatest(sa.started_at, w.t0)) / 3600 AS hours
    FROM mart.v_study_activity sa
    CROSS JOIN mart.v_current_week w
    WHERE sa.started_at < least(w.t1, now())
      AND sa.ended_at > w.t0
) x
WHERE hours > 0;
