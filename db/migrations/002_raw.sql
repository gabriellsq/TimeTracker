CREATE TABLE raw.timetagger_record (
    key         text PRIMARY KEY,
    payload     jsonb NOT NULL,
    server_ts   double precision NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);
