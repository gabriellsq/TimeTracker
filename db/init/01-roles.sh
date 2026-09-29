#!/usr/bin/env bash
# Run by the postgres image ONLY on first start with an empty data volume
# (/docker-entrypoint-initdb.d). Changing passwords later needs ALTER ROLE by hand.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v ingestor_pw="$INGESTOR_DB_PASSWORD" \
  -v grafana_pw="$GRAFANA_RO_DB_PASSWORD" <<'SQL'
CREATE ROLE ingestor LOGIN PASSWORD :'ingestor_pw';
CREATE ROLE grafana_ro LOGIN PASSWORD :'grafana_pw';
GRANT CONNECT, CREATE ON DATABASE lifelog TO ingestor;
GRANT CONNECT ON DATABASE lifelog TO grafana_ro;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
