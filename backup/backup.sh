#!/usr/bin/env bash
# Back up the stack's stateful data into a timestamped directory:
#   - Postgres: the core DB (files/versions/ACLs/roles/tenants) and the CSAI DB
#     (documents + per-tenant pgvector/FTS index),
#   - LDAP: the directory subtree (users, tenants, role groups) as portable LDIF,
#   - .env: the operator config INCLUDING AT_REST_KEY (kept 0600).
#
# NOT included (back these up separately — see backup/README.md):
#   - the external S3 bucket (the file CONTENT — the source of truth),
#   - TLS certs (re-issuable; or snapshot the `letsencrypt` volume).
#
# The compose stack must be running. Usage:  backup/backup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -f .env ] || { echo "backup: no .env in $ROOT"; exit 1; }
# Read values literally (compose .env allows unquoted spaces, e.g.
# LDAP_BIND_DN=cn=Directory Manager — which `source` would choke on). Last wins,
# matching compose.
geten() { grep -E "^$1=" .env | tail -1 | cut -d= -f2-; }
POSTGRES_USER="$(geten POSTGRES_USER)"
POSTGRES_PASSWORD="$(geten POSTGRES_PASSWORD)"
CORE_DB="$(geten CORE_DB)"
CSAI_DB="$(geten CSAI_DB)"
LDAP_DOMAIN="$(geten LDAP_DOMAIN)"
LDAP_BIND_DN="$(geten LDAP_BIND_DN)"
LDAP_BIND_PASSWORD="$(geten LDAP_BIND_PASSWORD)"
S3_BUCKET="$(geten S3_BUCKET)"

TS="$(date +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR:-$ROOT/backups}/$TS"
mkdir -p "$OUT"
dc() { docker compose "$@"; }

echo "[backup] -> $OUT"

echo "[backup] postgres: ${CORE_DB} + ${CSAI_DB}"
for db in "$CORE_DB" "$CSAI_DB"; do
  dc exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
     pg_dump -U "$POSTGRES_USER" -Fc "$db" > "$OUT/pg-${db}.dump"
  echo "  ✓ pg-${db}.dump ($(du -h "$OUT/pg-${db}.dump" | cut -f1))"
done

echo "[backup] ldap: ${LDAP_DOMAIN}"
dc exec -T ldap ldapsearch -LLL -o ldif-wrap=no -H ldap://localhost:3389 \
   -D "$LDAP_BIND_DN" -w "$LDAP_BIND_PASSWORD" -b "$LDAP_DOMAIN" "(objectClass=*)" '*' \
   > "$OUT/directory.ldif"
echo "  ✓ directory.ldif ($(grep -c '^dn:' "$OUT/directory.ldif") entries)"

cp ./.env "$OUT/env.backup"; chmod 600 "$OUT/env.backup"
echo "  ✓ env.backup (0600 — contains AT_REST_KEY; store securely)"

cat > "$OUT/MANIFEST.txt" <<EOF
FileEngine backup ${TS}
  pg-${CORE_DB}.dump   core metadata (files, versions, ACLs, roles, tenants)
  pg-${CSAI_DB}.dump   CSAI index (documents + per-tenant pgvector/FTS)
  directory.ldif       LDAP users / tenants / role groups
  env.backup           .env incl. AT_REST_KEY (SECRET)

NOT included — back up separately (see backup/README.md):
  - S3 bucket '${S3_BUCKET}' (file content; the source of truth)
  - TLS certs (re-issuable, or snapshot the 'letsencrypt' volume)
EOF

echo "[backup] done."
echo "[backup] REMINDER: also back up the S3 bucket and keep AT_REST_KEY safe"
echo "         (losing AT_REST_KEY makes the at-rest object data unreadable)."
