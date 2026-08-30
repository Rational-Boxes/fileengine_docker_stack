#!/usr/bin/env bash
# Emit an LDIF of `changetype: modify` operations that add every group member
# named in the export. Reads LDIF on stdin, writes modify-LDIF on stdout.
#
# WHY THIS EXISTS
#
# `ldapadd -c` skips an entry that already exists — and a groupOfNames entry IS
# its membership, so a group the seed created is never updated with the members
# the export holds. Restoring onto a stack that has been started (which is
# required, since ldap-init creates the suffix backend) therefore leaves every
# seeded role group with exactly the one member the seed put in it, and silently
# drops the rest.
#
# Observed on a restore rehearsal: share_external came back with 1 of its 4
# members. Nothing failed, nothing warned, and three people had quietly lost the
# role that lets them share files.
#
# One modify per member value, deliberately: 389-DS fails a whole multi-value
# add if any single value is already present, so batching them would make an
# already-correct group abort the ones after it. Paired with `ldapmodify -c`,
# per-value means an existing member is a no-op and a missing one is added.
set -euo pipefail

awk '
  /^ / { line = line substr($0, 2); next }
  NR > 1 { print line }
  { line = $0 }
  END { print line }
' | awk '
  # Keyed on the current dn, with NO objectClass test. An earlier version only
  # emitted for entries it had already seen a groupOfNames objectClass on, and
  # 389-DS does not guarantee attribute order — it emits objectClass AFTER member
  # for some entries and before for others, so three of five groups produced
  # nothing and the drop this script exists to prevent happened anyway.
  #
  # The test is unnecessary as well as fragile: in this schema an entry carrying
  # member or uniqueMember IS a group.
  /^dn:[ ]/ { dn = substr($0, 5); next }
  /^(member|uniqueMember):[ ]/ {
    if (dn == "") next
    p = index($0, ": ")
    printf "dn: %s\nchangetype: modify\nadd: %s\n%s: %s\n\n", dn, substr($0,1,p-1), substr($0,1,p-1), substr($0,p+2)
  }
'
