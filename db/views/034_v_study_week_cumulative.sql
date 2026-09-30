-- Points for the Study line chart: every hour of the current week plus "now".
-- actual_hours: cumulative study hours up to that point (NULL in the future).
-- pace_hours: goal spread evenly over the week (NULL when no goal is set).
CREATE VIEW mart.v_study_week_cumulative AS
WITH goal AS (
    SELECT sum(g.target_hours) AS goal_hours
    FROM core.goal g JOIN mart.v_current_week w ON g.week_start = w.week_start
), points AS (
    SELECT generate_series(w.t0, w.t1, interval '1 hour') AS t FROM mart.v_current_week w
    UNION
    SELECT now()
)
SELECT p.t AS time,
       CASE WHEN p.t <= now() THEN (
           SELECT coalesce(sum(extract(epoch FROM least(a.ended_at, p.t) - greatest(a.started_at, w.t0))), 0) / 3600
           FROM mart.v_study_week_activity a
           WHERE a.started_at < p.t
       ) END AS actual_hours,
       goal.goal_hours * extract(epoch FROM p.t - w.t0) / extract(epoch FROM w.t1 - w.t0) AS pace_hours
FROM points p
CROSS JOIN mart.v_current_week w
CROSS JOIN goal;
