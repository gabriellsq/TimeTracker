-- Current week study summary for the Study card.
-- goal_hours is the sum of this week's subject goals (NULL when none are set).
-- An all-zero goal counts as "no goal". "done" compares the displayed (rounded) total.
CREATE VIEW mart.v_study_week AS
SELECT s.*,
       s.week_label || ' · ' || s.sessions || CASE WHEN s.sessions = 1 THEN ' session' ELSE ' sessions' END
       || CASE WHEN s.goal_hours IS NULL THEN ' · no goal set'
               ELSE ' · goal ' || trim_scale(s.goal_hours) || ' h · ' || s.status END AS summary
FROM (
    SELECT w.week_start,
           w.week_start + 6 AS week_end,
           round(done.hours, 1) AS hours,
           done.sessions,
           goal.goal_hours,
           round(pace.expected, 1) AS expected_hours,
           CASE WHEN goal.goal_hours IS NULL THEN 'no goal'
                WHEN round(done.hours, 1) >= goal.goal_hours THEN 'done'
                WHEN done.hours >= pace.expected THEN 'ahead'
                ELSE 'behind' END AS status,
           to_char(w.week_start, 'Mon FMDD') || ' – ' || to_char(w.week_start + 6, 'Mon FMDD') AS week_label
    FROM mart.v_current_week w
    CROSS JOIN (
        SELECT coalesce(sum(hours), 0) AS hours, count(*) AS sessions FROM mart.v_study_week_activity
    ) done
    CROSS JOIN (
        SELECT nullif(sum(g.target_hours), 0) AS goal_hours
        FROM core.goal g JOIN mart.v_current_week cw ON g.week_start = cw.week_start
    ) goal
    CROSS JOIN LATERAL (
        SELECT goal.goal_hours * extract(epoch FROM least(now(), w.t1) - w.t0)
               / extract(epoch FROM w.t1 - w.t0) AS expected
    ) pace
) s;
