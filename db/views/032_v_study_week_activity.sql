-- Study activities overlapping the current week, with the hours that fall inside it.
CREATE VIEW mart.v_study_week_activity AS
SELECT sa.activity_id,
       sa.started_at,
       sa.ended_at,
       sa.subjects,
       extract(epoch FROM least(sa.ended_at, w.t1) - greatest(sa.started_at, w.t0)) / 3600 AS hours
FROM mart.v_study_activity sa
CROSS JOIN mart.v_current_week w
WHERE sa.started_at < w.t1
  AND sa.ended_at > w.t0;
