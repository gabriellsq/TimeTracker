-- Subjects: which tags count toward which study subject.
-- An activity matches a subject if one of its tags is in `tags` or starts with `tag_prefix`.
CREATE TABLE core.subject (
    subject     text PRIMARY KEY,
    label       text NOT NULL,
    tags        text[] NOT NULL DEFAULT '{}',
    tag_prefix  text,
    sort_order  smallint NOT NULL DEFAULT 0
);

INSERT INTO core.subject (subject, label, tags, tag_prefix, sort_order) VALUES
    ('ds', 'DS and Algorithms', '{ds}', 'ds/', 1),
    ('systemanalysis', 'System Analysis', '{systemanalysis}', NULL, 2);

-- Weekly goals, set on the /goals page. One row per week and subject.
CREATE TABLE core.goal (
    week_start    date NOT NULL CHECK (extract(isodow FROM week_start) = 1),
    subject       text NOT NULL REFERENCES core.subject,
    target_hours  numeric(4, 1) NOT NULL CHECK (target_hours >= 0 AND target_hours <= 40),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (week_start, subject)
);
