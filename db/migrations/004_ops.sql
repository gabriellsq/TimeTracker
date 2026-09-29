CREATE TABLE ops.sync_state (
    source           text PRIMARY KEY,
    cursor           text,
    last_success_at  timestamptz,
    last_error       text
);

CREATE TABLE ops.sync_run (
    run_id       bigserial PRIMARY KEY,
    source       text NOT NULL,
    trigger      text NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    n_records    integer,
    n_skipped    integer,
    status       text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    error        text
);

GRANT USAGE ON SCHEMA ops TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA ops TO grafana_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT SELECT ON TABLES TO grafana_ro;
