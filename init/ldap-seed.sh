#!/usr/bin/env bash
# Seed the 389-ds directory for the unified stack.
#
# Safe to re-run, and it does NOTHING on a directory that anything else has
# populated — a restore, most importantly. See the note at step 1b: re-seeding a
# restored directory used to grant system_admin to an account the source
# directory never gave it to.
#
#   - create the suffix backend + top entry if absent (the 389ds image creates
#     neither automatically),
#   - add ou=users (uid=email), ou=tenants, and the default tenant OU with its
#     role groups (groupOfNames: users / contributors / administrators /
#     system_admin / share_external),
#   - add the initial admin user as a member of all five default-tenant groups.
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

# 1b. SEED ONCE, and never into a directory something else has populated.
#
# `ldapadd -c` skips entries that already exist, which made this look safe to
# re-run — and it is, on a directory only this script has ever written to. It is
# NOT safe on a RESTORED one.
#
# A directory restored from another deployment holds that deployment's role
# groups. It does not hold the ones below that it never had, so re-running this
# ADDS them: on a real restore that meant cn=users, cn=contributors and
# cn=system_admin appearing out of nowhere, each with the local admin as a
# member. system_admin is the GLOBAL PRIVILEGE BYPASS, so the restored copy came
# out more privileged than the system it was copied from, granted to an account
# whose password is a deploy secret rather than anything in the source
# directory. Nothing failed and nothing warned.
#
# It is not a rare path. ldap-init is a compose dependency of csai-app,
# discussion and the rest, so ANY later `compose up` re-runs it — an ordinary
# redeploy is enough.
#
# The test is not "is the directory empty": a run that died halfway must still be
# able to finish. It is "does the directory contain anything this script did not
# put here". Every entry it creates is known and listed below, so an entry
# outside that set means somebody else — a restore, or an administrator — owns
# this directory, and seeding it is not our business. A partially seeded
# directory contains only our own entries and is completed normally.
_seed_dns="$(printf '%s\n' \
  "$SUFFIX" "$USER_BASE" "$TENANT_BASE" "ou=${TENANT},${TENANT_BASE}" "$ADMIN_DN" \
  "cn=users,ou=${TENANT},${TENANT_BASE}" \
  "cn=contributors,ou=${TENANT},${TENANT_BASE}" \
  "cn=administrators,ou=${TENANT},${TENANT_BASE}" \
  "cn=system_admin,ou=${TENANT},${TENANT_BASE}" \
  "cn=share_external,ou=${TENANT},${TENANT_BASE}" \
  | tr 'A-Z' 'a-z' | sed 's/, */,/g' | sort)"

# Unfolded with awk rather than with `-o ldif-wrap=no`, and the exit status is
# checked rather than discarded. Both matter for the same reason: every way this
# lookup can quietly return nothing makes _foreign empty, which lets the seed run
# — the exact outcome the guard exists to prevent. A guard that fails open is
# not a guard, so it fails closed instead.
_present_raw="$(ldapsearch -x -H "$LDAP_URI" -D "$DM_DN" -w "$DM_PW" \
                  -b "$SUFFIX" -LLL "(objectClass=*)" dn 2>&1)" || {
  echo "ldap-seed: could not read ${SUFFIX} to check whether it is already populated" >&2
  printf '%s\n' "$_present_raw" | tail -2 >&2
  echo "ldap-seed: refusing to seed blind — a seed into a restored directory grants system_admin" >&2
  exit 1
}

# NO AWK IN THIS FILE. The 389ds/dirsrv image this runs in does not ship awk or
# gawk, and the failure is `awk: command not found`, exit 127 — raised after the
# seed has already waited for the directory, so it reads as a directory problem
# rather than a missing tool. sed, tr, sort, comm, head, wc and grep are all
# present; so is python3, if something here ever genuinely needs it.
#
# `:a;N;$!ba;s/\n //g` is LDIF unfolding: slurp the output, then join every
# newline followed by a space onto the line before it. A dn long enough to be
# folded would otherwise match nothing in the set above and be misread as
# foreign.
_present="$(printf '%s\n' "$_present_raw" \
            | sed ':a;N;$!ba;s/\n //g' \
            | sed -n 's/^dn: //p' | tr 'A-Z' 'a-z' | sed 's/, */,/g' | sort)"

_foreign="$(comm -23 <(printf '%s\n' "$_present") <(printf '%s\n' "$_seed_dns") | sed '/^$/d')"

if [ -n "$_foreign" ]; then
  echo "ldap-seed: this directory already holds entries this seed did not create:"
  printf '  %s\n' $_foreign | head -5
  _n="$(printf '%s\n' "$_foreign" | wc -l)"
  [ "$_n" -gt 5 ] && echo "  ... and $((_n - 5)) more"
  echo "ldap-seed: leaving it alone — seeding now would add role groups it does"
  echo "ldap-seed: not have, system_admin among them. Nothing to do."
  exit 0
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

dn: cn=share_external,ou=${TENANT},${TENANT_BASE}
objectClass: top
objectClass: groupOfNames
cn: share_external
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
