-- Hours and goal per subject for the current week. An activity with two subjects counts for both.
-- Hours tagged only #study (no subject) count in the weekly total but in no subject row.
CREATE VIEW mart.v_study_subject_week AS
SELECT s.subject,
       s.label,
       s.sort_order,
       round(coalesce(h.hours, 0), 1) AS hours,
       g.target_hours AS goal_hours,
       s.label || ' · ' || trim_scale(round(coalesce(h.hours, 0), 1)) || ' / '
           || coalesce(trim_scale(g.target_hours)::text, '–') || ' h' AS display
FROM core.subject s
CROSS JOIN mart.v_current_week w
LEFT JOIN core.goal g ON g.week_start = w.week_start AND g.subject = s.subject
LEFT JOIN (
    SELECT subj AS subject, sum(a.hours) AS hours
    FROM mart.v_study_week_activity a
    CROSS JOIN LATERAL unnest(a.subjects) AS subj
    GROUP BY subj
) h ON h.subject = s.subject;
