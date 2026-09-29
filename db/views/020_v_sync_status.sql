-- Latest sync run per source, for the dashboard status tile.
CREATE VIEW mart.v_sync_status AS
SELECT
    source,
    status,
    started_at,
    finished_at,
    n_records,
    n_skipped,
    error,
    floor(extract(epoch FROM now() - coalesce(finished_at, started_at)) / 60)::int AS minutes_ago
FROM (
    SELECT DISTINCT ON (source) *
    FROM ops.sync_run
    ORDER BY source, started_at DESC, run_id DESC
) latest;
