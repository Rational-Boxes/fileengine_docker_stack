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
# ENV_FILE lets this run against a stack whose environment is not the checked-in
# .env — a scratch instance stood up to REHEARSE a restore, most usefully, which
# is the one thing that proves a backup is worth anything. Combine with compose's
# own COMPOSE_PROJECT_NAME / COMPOSE_FILE to point at that instance:
#
#   ENV_FILE=/path/restore.env COMPOSE_PROJECT_NAME=fileengine-restore \
#     backup/restore.sh backups/<timestamp>
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
[ -f "$ENV_FILE" ] || { echo "restore: no env file at '$ENV_FILE'"; exit 1; }

# Read values literally (compose .env allows unquoted spaces; last wins).
geten() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2-; }
POSTGRES_USER="$(geten POSTGRES_USER)"
POSTGRES_PASSWORD="$(geten POSTGRES_PASSWORD)"
CORE_DB="$(geten CORE_DB)"
CSAI_DB="$(geten CSAI_DB)"
LDAP_BIND_DN="$(geten LDAP_BIND_DN)"
LDAP_BIND_PASSWORD="$(geten LDAP_BIND_PASSWORD)"
LDAP_ADMIN_EMAIL="$(geten LDAP_ADMIN_EMAIL)"
LDAP_USER_BASE="$(geten LDAP_USER_BASE)"
dc() { docker compose --env-file "$ENV_FILE" "$@"; }

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
  # Filtered so the restore cannot displace THIS stack's administrator.
  #
  # An export from another deployment normally holds the same person at a
  # different dn, because that deployment seeded its admin from a different
  # value. Replayed as-is, two entries then carry the address used to log in,
  # the bridge's lookup returns both, and it refuses the ambiguity — every
  # login fails, including the operator's, after the restore has already
  # reported success and with no account left to fix it from. The filter drops
  # the incoming duplicate and repoints its group memberships at the local
  # admin, so the account in .env stays valid and keeps the source admin's
  # roles. See backup/ldif-preserve-local-admin.sh.
  echo "[restore] ldap (idempotent ldapadd -c, local admin preserved)"
  _ldif="$(mktemp)"; trap 'rm -f "$_ldif"' EXIT
  LDAP_ADMIN_EMAIL="$LDAP_ADMIN_EMAIL" LDAP_USER_BASE="$LDAP_USER_BASE" \
    "$ROOT/backup/ldif-preserve-local-admin.sh" < "$IN/directory.ldif" > "$_ldif"

  dc exec -T ldap ldapadd -x -c -H ldap://localhost:3389 \
     -D "$LDAP_BIND_DN" -w "$LDAP_BIND_PASSWORD" < "$_ldif" \
     || echo "  (ldapadd -c: existing entries skipped)"

  # Second pass, and it is not optional.
  #
  # ldapadd -c skips an entry that already exists, and a groupOfNames entry IS
  # its membership — so every role group the seed created keeps exactly the one
  # member the seed gave it, and the rest of the export's membership is dropped.
  # Nothing fails and nothing warns. Measured on a rehearsal: share_external came
  # back holding 1 of its 4 members, so three people had silently lost the role
  # that lets them share files.
  #
  # This adds each member the export names. Values already present come back as
  # "Type or value exists" and -c carries on, so it is safe to re-run.
  echo "[restore] ldap: merging group membership the add pass could not update"
  "$ROOT/backup/ldif-merge-group-members.sh" < "$_ldif" \
    | dc exec -T ldap ldapmodify -x -c -H ldap://localhost:3389 \
        -D "$LDAP_BIND_DN" -w "$LDAP_BIND_PASSWORD" 2>&1 \
    | grep -vE "Type or value exists|^modifying entry" || true

  # Prove the thing this is all for: exactly one entry answers to the
  # configured address. Two is the lockout, and it is much better found now,
  # with a shell open, than at the next sign-in.
  _n="$(dc exec -T ldap ldapsearch -x -H ldap://localhost:3389 \
          -D "$LDAP_BIND_DN" -w "$LDAP_BIND_PASSWORD" -b "$LDAP_USER_BASE" -LLL \
          "(|(uid=$LDAP_ADMIN_EMAIL)(mail=$LDAP_ADMIN_EMAIL))" dn 2>/dev/null \
        | grep -c '^dn:' || true)"
  if [ "${_n:-0}" != "1" ]; then
    echo "[restore] FAILED — ${_n} directory entries answer to $LDAP_ADMIN_EMAIL." >&2
    echo "          Exactly one must, or the bridge refuses every login for it." >&2
    exit 1
  fi
  echo "[restore] ldap: one entry answers to $LDAP_ADMIN_EMAIL — sign-in is intact"
fi

echo "[restore] done. File content is served from S3; no per-file restore needed"
echo "          unless the bucket itself was lost (then restore it first)."
