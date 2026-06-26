#!/usr/bin/env bash
# Seed the 389-ds directory for the unified stack (idempotent; safe to re-run).
#
#   - create the suffix backend + top entry if absent (the 389ds image creates
#     neither automatically),
#   - add ou=users (uid=email), ou=tenants, and the default tenant OU with its
#     role groups (groupOfNames: users / contributors / administrators /
#     system_admin),
#   - add the initial admin user as a member of all four default-tenant groups.
#
# New tenants are added later with scripts/new-tenant.sh. Per-tenant DB schemas /
# storage are created by the core on first access.
set -euo pipefail

LDAP_URI="${LDAP_URI:-ldap://ldap:3389}"
SUFFIX="${LDAP_SUFFIX:?LDAP_SUFFIX required}"          # e.g. dc=example,dc=com
DM_DN="${LDAP_BIND_DN:-cn=Directory Manager}"
DM_PW="${LDAP_BIND_PASSWORD:?LDAP_BIND_PASSWORD required}"
TENANT="${DEFAULT_TENANT:-default}"
ADMIN_EMAIL="${LDAP_ADMIN_EMAIL:?LDAP_ADMIN_EMAIL required}"
ADMIN_PW="${LDAP_ADMIN_PASSWORD:?LDAP_ADMIN_PASSWORD required}"

USER_BASE="ou=users,${SUFFIX}"
TENANT_BASE="ou=tenants,${SUFFIX}"
ADMIN_DN="uid=${ADMIN_EMAIL},${USER_BASE}"

echo "ldap-seed: waiting for ${LDAP_URI} ..."
until ldapsearch -H "$LDAP_URI" -x -D "$DM_DN" -w "$DM_PW" -b "cn=config" -s base dn >/dev/null 2>&1; do
  sleep 2
done

# 1. Backend + suffix top entry (the image sets DS_SUFFIX_NAME but creates no
#    backend). --create-suffix also adds the dc=… top entry.
if ! dsconf "$LDAP_URI" -D "$DM_DN" -w "$DM_PW" backend suffix list 2>/dev/null | grep -qiF "$SUFFIX"; then
  echo "ldap-seed: creating backend for ${SUFFIX}"
  dsconf "$LDAP_URI" -D "$DM_DN" -w "$DM_PW" backend create \
    --suffix "$SUFFIX" --be-name userroot --create-suffix
fi

# 2. DIT + admin user + role groups (idempotent: ldapadd -c, treat "Already
#    exists"/rc 68 as benign).
set +e
out="$(ldapadd -x -c -H "$LDAP_URI" -D "$DM_DN" -w "$DM_PW" 2>&1 <<LDIF
dn: ${USER_BASE}
objectClass: top
objectClass: organizationalUnit
ou: users

dn: ${TENANT_BASE}
objectClass: top
objectClass: organizationalUnit
ou: tenants

dn: ou=${TENANT},${TENANT_BASE}
objectClass: top
objectClass: organizationalUnit
ou: ${TENANT}

dn: ${ADMIN_DN}
objectClass: top
objectClass: inetOrgPerson
uid: ${ADMIN_EMAIL}
cn: ${ADMIN_EMAIL}
sn: Administrator
mail: ${ADMIN_EMAIL}
userPassword: ${ADMIN_PW}

dn: cn=users,ou=${TENANT},${TENANT_BASE}
objectClass: top
objectClass: groupOfNames
cn: users
member: ${ADMIN_DN}

dn: cn=contributors,ou=${TENANT},${TENANT_BASE}
objectClass: top
objectClass: groupOfNames
cn: contributors
member: ${ADMIN_DN}

dn: cn=administrators,ou=${TENANT},${TENANT_BASE}
objectClass: top
objectClass: groupOfNames
cn: administrators
member: ${ADMIN_DN}

dn: cn=system_admin,ou=${TENANT},${TENANT_BASE}
objectClass: top
objectClass: groupOfNames
cn: system_admin
member: ${ADMIN_DN}
LDIF
)"
rc=$?
set -e
printf '%s\n' "$out"
if [ "$rc" -ne 0 ] && ! grep -qi "Already exists" <<<"$out"; then
  echo "ldap-seed: ldapadd failed (rc=$rc)" >&2
  exit 1
fi

echo "ldap-seed: done (suffix=${SUFFIX}, tenant=${TENANT}, admin=${ADMIN_EMAIL})"
