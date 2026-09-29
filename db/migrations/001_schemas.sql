-- ops is created by the migration runner itself (it stores ops.schema_migration).
-- mart is dropped and rebuilt from db/views on every start.
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
