#!/usr/bin/env bash
# Rewrite a directory export so restoring it CANNOT displace this stack's own
# LDAP administrator. Reads LDIF on stdin, writes LDIF on stdout.
#
#   LDAP_ADMIN_EMAIL=... LDAP_USER_BASE=... ldif-preserve-local-admin.sh < in > out
#
# WHY THIS EXISTS
#
# The stack seeds its administrator from .env at uid=<LDAP_ADMIN_EMAIL>. A backup
# taken from a different deployment usually holds the same person at a DIFFERENT
# dn — uid=james carrying mail: james@example.com, say — because that deployment
# was seeded from a different value. Replaying such an export leaves TWO entries
# whose mail matches the address used to log in, the bridge's user lookup returns
# both, and it refuses an ambiguous result:
#
#   LDAPAuthenticator::authenticateUser: User not found or multiple entries
#   found (count: 2)          ->  POST /v1/auth/token 401
#
# Every login fails, including the operator's, and it fails AFTER the restore has
# reported success. There is then no account left with which to fix it from the
# outside. This was reproduced twice during a restore rehearsal on 2026-08-30.
#
# WHAT IT DOES
#
# Where the incoming export claims this stack's admin address at some other dn:
#
#   - that entry is DROPPED, so the local admin entry and the .env password it
#     was seeded with are left exactly as they are, and
#   - every reference to the dropped dn — member, uniqueMember, owner, manager,
#     seeAlso, roleOccupant — is repointed at the local admin dn, so the role
#     groups keep their membership and no dangling reference is left behind.
#
# The result is one entry for that address, holding the credential the operator
# already has, in all the groups the source admin belonged to.
#
# Everything else in the export passes through untouched. If the export's admin
# dn already matches the local one, this is a no-op: ldapadd -c skips an entry
# that exists, so the local password survives that case on its own.
set -euo pipefail

ADMIN_EMAIL="${LDAP_ADMIN_EMAIL:?LDAP_ADMIN_EMAIL required}"
USER_BASE="${LDAP_USER_BASE:?LDAP_USER_BASE required}"
ADMIN_DN="uid=${ADMIN_EMAIL},${USER_BASE}"

tmp="$(mktemp)"; trap 'rm -f "$tmp" "$tmp.u"' EXIT
cat > "$tmp"

# Unfold first. LDIF wraps long values onto continuation lines beginning with a
# space, and a dn or a member value split across two lines matches nothing at
# all — the filter would pass the colliding entry straight through and the
# lockout would happen anyway, silently.
awk '
  /^ / { line = line substr($0, 2); next }
  NR > 1 { print line }
  { line = $0 }
  END { print line }
' "$tmp" > "$tmp.u"

# Which dn — if any — claims the local admin address from somewhere else?
# Matched on mail and on uid, since either is what the bridge looks the user up
# by, and case-insensitively, because an address is not case sensitive and a
# capitalised copy would collide just as effectively.
FOREIGN_DN="$(awk -v admin="$ADMIN_EMAIL" -v admindn="$ADMIN_DN" '
  BEGIN { IGNORECASE = 1; want = tolower(admin); wantdn = tolower(admindn) }
  /^dn:[ ]/ { dn = substr($0, 5); next }
  /^(mail|uid):[ ]/ {
    v = tolower(substr($0, index($0, ": ") + 2))
    if (v == want && tolower(dn) != wantdn && dn != "") { print dn; exit }
  }
' "$tmp.u")"

if [ -z "$FOREIGN_DN" ]; then
  echo "preserve-local-admin: nothing to do — no other entry claims ${ADMIN_EMAIL}" >&2
  cat "$tmp.u"
  exit 0
fi

echo "preserve-local-admin: '${FOREIGN_DN}' also claims ${ADMIN_EMAIL}" >&2
echo "preserve-local-admin: dropping it and repointing its references at ${ADMIN_DN}" >&2

awk -v foreign="$FOREIGN_DN" -v admindn="$ADMIN_DN" '
  BEGIN { drop = 0; dropped = 0; repointed = 0 }
  /^dn:[ ]/ { drop = (substr($0, 5) == foreign); if (drop) dropped++ }
  # A blank line ends the record, so it also ends any skipping. Emitting it keeps
  # the record separation intact for the entries that follow.
  /^$/ { drop = 0; print; next }
  {
    if (drop) next
    p = index($0, ": ")
    if (p > 0) {
      attr = substr($0, 1, p - 1); val = substr($0, p + 2)
      if (val == foreign && attr ~ /^(member|uniqueMember|owner|manager|seeAlso|roleOccupant)$/) {
        print attr ": " admindn; repointed++; next
      }
    }
    print
  }
  END {
    printf "preserve-local-admin: dropped %d entry, repointed %d reference(s)\n", dropped, repointed > "/dev/stderr"
  }
' "$tmp.u"
