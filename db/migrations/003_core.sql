CREATE TABLE core.activity (
    activity_id        bigserial PRIMARY KEY,
    source             text NOT NULL,
    source_id          text NOT NULL,
    started_at         timestamptz NOT NULL,
    ended_at           timestamptz,
    description        text,
    is_deleted         boolean NOT NULL DEFAULT false,
    source_updated_at  timestamptz,
    ingested_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, source_id),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE TABLE core.activity_tag (
    activity_id  bigint NOT NULL REFERENCES core.activity ON DELETE CASCADE,
    tag          text NOT NULL,
    position     smallint NOT NULL,
    PRIMARY KEY (activity_id, tag)
);

CREATE INDEX activity_started_at_idx ON core.activity (started_at);
CREATE INDEX activity_tag_tag_idx ON core.activity_tag (tag);
