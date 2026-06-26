#!/usr/bin/env bash
#
# new-tenant.sh — create a new tenant definition in the FileEngine LDAP directory.
#
# Adds, under the configured tenant base:
#     ou=<tenant>,<tenant_base>                       (organizationalUnit)
#     cn=<role>,ou=<tenant>,<tenant_base>             (groupOfNames) for each role
#
# The core service auto-creates the tenant's database schema and storage folder
# on first access, so this script provisions only the directory side.
#
# Connection settings default to the FILEENGINE_LDAP_* environment — the same
# variables the bridges use — and can be overridden by env or flags.
#
# Usage:
#   new-tenant.sh <tenant> [--admin <email>] [options]
#
# Options:
#   --admin <email>      Seed an existing user (uid=<email>,<user_base>) as the
#                        tenant's initial member of every role group. If omitted,
#                        the bind DN is used as a schema placeholder (groupOfNames
#                        requires >=1 member); replace it via the admin UI.
#   --roles "a b c"      Role groups to create (default: "users contributors
#                        administrators system_admin"; or $TENANT_ROLES).
#   --endpoint <uri>     LDAP URI         (FILEENGINE_LDAP_ENDPOINT)
#   --domain <dn>        Base domain DN   (FILEENGINE_LDAP_DOMAIN)
#   --bind-dn <dn>       Bind DN          (FILEENGINE_LDAP_BIND_DN)
#   --password <pw>      Bind password    (FILEENGINE_LDAP_BIND_PASSWORD; prompted if unset)
#   --tenant-base <dn>   Tenant base      (FILEENGINE_LDAP_TENANT_BASE)
#   --user-base <dn>     User base        (FILEENGINE_LDAP_USER_BASE)
#   --container <name>   Run ldapadd inside this LDAP container (docker/podman exec)
#                        when ldapadd is not on PATH (e.g. LDAP_CONTAINER=openldap).
#   --dry-run            Print the LDIF and exit without applying.
#   -h, --help           Show this help.
#
set -euo pipefail

DOMAIN="${FILEENGINE_LDAP_DOMAIN:-dc=rationalboxes,dc=com}"
ENDPOINT="${FILEENGINE_LDAP_ENDPOINT:-ldap://localhost:1389}"
BIND_DN="${FILEENGINE_LDAP_BIND_DN:-cn=admin,${DOMAIN}}"
BIND_PW="${FILEENGINE_LDAP_BIND_PASSWORD:-}"
TENANT_BASE="${FILEENGINE_LDAP_TENANT_BASE:-ou=tenants,${DOMAIN}}"
USER_BASE="${FILEENGINE_LDAP_USER_BASE:-ou=users,${DOMAIN}}"
ROLES_DEFAULT="${TENANT_ROLES:-users contributors administrators system_admin}"
CONTAINER="${LDAP_CONTAINER:-}"

ADMIN_EMAIL=""
ROLES="$ROLES_DEFAULT"
DRY_RUN=0
TENANT=""

die() { echo "error: $*" >&2; exit 1; }
usage() { sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --admin)       ADMIN_EMAIL="${2:?}"; shift 2;;
    --roles)       ROLES="${2:?}"; shift 2;;
    --endpoint)    ENDPOINT="${2:?}"; shift 2;;
    --domain)      DOMAIN="${2:?}"; shift 2;;
    --bind-dn)     BIND_DN="${2:?}"; shift 2;;
    --password)    BIND_PW="${2:?}"; shift 2;;
    --tenant-base) TENANT_BASE="${2:?}"; shift 2;;
    --user-base)   USER_BASE="${2:?}"; shift 2;;
    --container)   CONTAINER="${2:?}"; shift 2;;
    --dry-run)     DRY_RUN=1; shift;;
    -h|--help)     usage 0;;
    -*)            die "unknown option: $1";;
    *)             [ -z "$TENANT" ] || die "unexpected argument: $1"; TENANT="$1"; shift;;
  esac
done

[ -n "$TENANT" ] || { echo "missing <tenant>"; usage 1; }
# Tenant names become an RDN value, a DB schema name, and a subdomain label.
# No hyphen: the WebDAV host <tenant>-drive.<base> is split on '-' to recover the
# tenant (first segment), so a hyphen in the name would be ambiguous.
[[ "$TENANT" =~ ^[A-Za-z0-9._]+$ ]] || die "invalid tenant name '$TENANT' (allowed: A-Z a-z 0-9 . _ ; no hyphen)"

# groupOfNames requires at least one member; use the admin user or the bind DN.
if [ -n "$ADMIN_EMAIL" ]; then
  MEMBER="uid=${ADMIN_EMAIL},${USER_BASE}"
else
  MEMBER="$BIND_DN"
fi

# Build the LDIF (OU + one groupOfNames per role).
LDIF="dn: ou=${TENANT},${TENANT_BASE}
objectClass: organizationalUnit
objectClass: top
ou: ${TENANT}
"
for role in $ROLES; do
  LDIF+="
dn: cn=${role},ou=${TENANT},${TENANT_BASE}
objectClass: groupOfNames
objectClass: top
cn: ${role}
member: ${MEMBER}
"
done

if [ "$DRY_RUN" -eq 1 ]; then
  printf '%s\n' "$LDIF"
  exit 0
fi

# Prompt for the bind password if it wasn't supplied (avoids putting it in env/args).
if [ -z "$BIND_PW" ]; then
  read -r -s -p "Bind password for ${BIND_DN}: " BIND_PW; echo
fi

# Apply via ldapadd. `-c` continues past entries that already exist so re-running
# is safe (existing OU/groups are skipped; new ones are added).
apply() {
  if command -v ldapadd >/dev/null 2>&1; then
    ldapadd -x -c -H "$ENDPOINT" -D "$BIND_DN" -w "$BIND_PW"
  elif [ -n "$CONTAINER" ]; then
    local oci; oci="$(command -v docker || command -v podman)" || die "need docker or podman for --container"
    "$oci" exec -i "$CONTAINER" ldapadd -x -c -H ldap://localhost:389 -D "$BIND_DN" -w "$BIND_PW"
  else
    die "ldapadd not found; install openldap-clients or pass --container <ldap container>"
  fi
}

set +e
out="$(printf '%s' "$LDIF" | apply 2>&1)"; rc=$?
set -e
printf '%s\n' "$out"
# rc 0 = all added; ldapadd -c exits 68 if some entries already existed (benign).
if [ "$rc" -ne 0 ] && ! grep -qi "Already exists" <<<"$out"; then
  die "ldapadd failed (rc=$rc)"
fi

echo
echo "✓ tenant '${TENANT}' provisioned in LDAP under ${TENANT_BASE}"
echo "  roles: ${ROLES}"
[ -n "$ADMIN_EMAIL" ] && echo "  initial member: ${MEMBER}" \
  || echo "  (seeded with placeholder member ${MEMBER}; add real members via the admin UI)"
echo "  The core service will create the DB schema + storage folder on first access."
