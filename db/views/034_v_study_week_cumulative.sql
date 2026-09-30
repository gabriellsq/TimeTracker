-- Points for the Study line chart: every hour of the current week plus "now".
-- actual_hours: cumulative study hours up to that point (NULL in the future).
-- pace_hours: goal spread evenly over the week (NULL when no goal is set).
-- week_activity is materialized once; the per-point subquery would otherwise recompute it ~170 times.
CREATE VIEW mart.v_study_week_cumulative AS
WITH goal AS (
    SELECT nullif(sum(g.target_hours), 0) AS goal_hours
    FROM core.goal g JOIN mart.v_current_week w ON g.week_start = w.week_start
), week_activity AS MATERIALIZED (
    SELECT started_at, ended_at FROM mart.v_study_week_activity
), points AS (
    SELECT generate_series(w.t0, w.t1, interval '1 hour') AS t FROM mart.v_current_week w
    UNION
    SELECT now()
)
SELECT p.t AS time,
       CASE WHEN p.t <= now() THEN (
           SELECT coalesce(sum(extract(epoch FROM least(a.ended_at, p.t) - greatest(a.started_at, w.t0))), 0) / 3600
           FROM week_activity a
           WHERE a.started_at < p.t
       ) END AS actual_hours,
       goal.goal_hours * extract(epoch FROM p.t - w.t0) / extract(epoch FROM w.t1 - w.t0) AS pace_hours
FROM points p
CROSS JOIN mart.v_current_week w
CROSS JOIN goal;
