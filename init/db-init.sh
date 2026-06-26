#!/usr/bin/env bash
# One-shot data-layer initialization (idempotent; safe to re-run).
#
#  - The core database (CORE_DB) is created by the postgres image (POSTGRES_DB).
#  - Create the CSAI database (CSAI_DB) if absent — CREATE DATABASE has no
#    IF NOT EXISTS, so guard it.
#  - Install the DB-wide extensions CSAI needs (vector, pg_trgm) — mirrors
#    convert_search_ai/migrations/0001_baseline.sql; also applies any extra
#    staged migration SQL mounted at /migrations.
#
# No per-tenant DDL here: the core auto-creates its schema + default tenant on
# first access, and CSAI provisions per-tenant tables (documents/chunks) on
# demand. So this only lays down databases + extensions.
set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD required}"
PGHOST="${POSTGRES_HOST:-postgres}"
PGPORT="${POSTGRES_PORT:-5432}"
CORE_DB="${CORE_DB:-fileengine}"
CSAI_DB="${CSAI_DB:-convert_search_ai}"
export PGPASSWORD="$POSTGRES_PASSWORD"

psql_admin() { psql -v ON_ERROR_STOP=1 -h "$PGHOST" -p "$PGPORT" -U "$POSTGRES_USER" "$@"; }
db_exists()  { [ "$(psql_admin -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$1'")" = "1" ]; }

echo "db-init: waiting for postgres at $PGHOST:$PGPORT ..."
until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$POSTGRES_USER" >/dev/null 2>&1; do sleep 1; done

for db in "$CORE_DB" "$CSAI_DB"; do
  if db_exists "$db"; then
    echo "db-init: database '$db' already exists"
  else
    echo "db-init: creating database '$db'"
    psql_admin -d postgres -c "CREATE DATABASE \"$db\""
  fi
done

echo "db-init: installing extensions in '$CSAI_DB' (vector, pg_trgm)"
psql_admin -d "$CSAI_DB" -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"

if compgen -G "/migrations/*.sql" >/dev/null 2>&1; then
  for f in /migrations/*.sql; do
    echo "db-init: applying $(basename "$f") to '$CSAI_DB'"
    psql_admin -d "$CSAI_DB" -f "$f"
  done
fi

echo "db-init: done"
