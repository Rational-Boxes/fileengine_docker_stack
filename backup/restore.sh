#!/usr/bin/env bash
# Restore Postgres + LDAP from a directory produced by backup.sh.
#
# The stack must be running first (db-init/ldap-init create the databases and the
# LDAP suffix backend; this restores DATA into them). Restoring file CONTENT is a
# separate step — it lives in S3 (see backup/README.md).
#
# Usage:  backup/restore.sh <backup-dir>
set -euo pipefail

IN="${1:?usage: restore.sh <backup-dir>}"
[ -d "$IN" ] || { echo "restore: '$IN' is not a directory"; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# Read values literally (compose .env allows unquoted spaces; last wins).
geten() { grep -E "^$1=" .env | tail -1 | cut -d= -f2-; }
POSTGRES_USER="$(geten POSTGRES_USER)"
POSTGRES_PASSWORD="$(geten POSTGRES_PASSWORD)"
CORE_DB="$(geten CORE_DB)"
CSAI_DB="$(geten CSAI_DB)"
LDAP_BIND_DN="$(geten LDAP_BIND_DN)"
LDAP_BIND_PASSWORD="$(geten LDAP_BIND_PASSWORD)"
dc() { docker compose "$@"; }

echo "[restore] from $IN (stack must be up)"

for db in "$CORE_DB" "$CSAI_DB"; do
  f="$IN/pg-${db}.dump"
  [ -f "$f" ] || { echo "  - skip $db (no dump)"; continue; }
  echo "[restore] postgres $db (pg_restore --clean --if-exists)"
  if ! dc exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
        pg_restore -U "$POSTGRES_USER" -d "$db" --clean --if-exists --no-owner < "$f"; then
    echo "  (pg_restore reported warnings — usually benign DROP-of-absent-object)"
  fi
done

if [ -f "$IN/directory.ldif" ]; then
  echo "[restore] ldap (idempotent ldapadd -c)"
  dc exec -T ldap ldapadd -x -c -H ldap://localhost:3389 \
     -D "$LDAP_BIND_DN" -w "$LDAP_BIND_PASSWORD" < "$IN/directory.ldif" \
     || echo "  (ldapadd -c: existing entries skipped)"
fi

echo "[restore] done. File content is served from S3; no per-file restore needed"
echo "          unless the bucket itself was lost (then restore it first)."
