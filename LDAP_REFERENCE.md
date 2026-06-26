# LDAP Directory Reference & Templating Plan

Captured by interrogating the **development** LDAP directory (the example the
current bridge code is built against), plus the plan to make the bridges' LDAP
access **templated and site-configurable** rather than hard-coded.

---

## 1. Development directory (reference layout)

**Server (dev):** `osixia/openldap:1.5.0` on `ldap://localhost:1389`
(container `openldap`), with a `wheelybird/ldap-user-manager` admin UI on
`:18080` (container `openldap-ui`).

**Connection settings** live in `http_bridge/.env` and `webdav_bridge/.env`
(dev infrastructure settings):

| Env var | Dev value |
|---------|-----------|
| `FILEENGINE_LDAP_ENDPOINT` | `ldap://localhost:1389` |
| `FILEENGINE_LDAP_DOMAIN` | `dc=rationalboxes,dc=com` |
| `FILEENGINE_LDAP_BIND_DN` | `cn=admin,dc=rationalboxes,dc=com` |
| `FILEENGINE_LDAP_BIND_PASSWORD` | *(secret)* |
| `FILEENGINE_LDAP_TENANT_BASE` | `ou=tenants,dc=rationalboxes,dc=com` |
| `FILEENGINE_LDAP_USER_BASE` | `ou=users,dc=rationalboxes,dc=com` |

**Observed DIT (multitenancy layout):**

```
dc=rationalboxes,dc=com
├── ou=users                                            # person entries
│   └── uid=testuser@rationalboxes.com                  # objectClass: inetOrgPerson
│         cn: Test User                                 #   uid == mail == full email
│         sn: User
│         mail: testuser@rationalboxes.com
│         uid: testuser@rationalboxes.com
└── ou=tenants                                          # all tenants
    ├── ou=default                                      # one tenant = one OU
    │   ├── cn=users          (groupOfNames)            # role group; member = full user DN
    │   ├── cn=contributors   (groupOfNames)
    │   └── cn=administrators (groupOfNames)
    └── ou=filenginetest
        ├── cn=users          (groupOfNames)
        └── cn=administrators (groupOfNames)
```

**Conventions observed:**
- **Users** live under `ou=users`; the **`uid` is the full email** (`uid == mail`);
  objectClass `inetOrgPerson`.
- **Tenants** are OUs under `ou=tenants` (`default`, `filenginetest`).
- **Roles** are per-tenant **`groupOfNames`** whose `cn` is the role name; the
  **`member`** attribute holds the **full user DN**
  (`uid=<email>,ou=users,dc=…`).
- A user's **tenants and roles are derived from group membership**: `testuser` is
  a member of `users`/`contributors`/`administrators` in `default`, and
  `users`/`administrators` in `filenginetest`.
- Role names seen in dev: `users`, `contributors`, `administrators`.
  (`system_admin` is part of the broader model but is **not present** in the dev
  directory — illustrating that the role set itself varies by site.)

> Note: the agreed model elsewhere referred to `ou=people`; the **actual** dev
> directory uses **`ou=users`**. This mismatch is exactly why the lookups must be
> configurable, not hard-coded.

---

## 2. Assessment — deployment config is largely already covered

Reviewing `http_bridge/src/ldap_authenticator.cpp` (shared with the WebDAV
bridge): the authenticator is **already substantially site-configurable**, not
hard-coded to the example directory. A new deployment can usually be pointed at
its own directory with **just the existing env fields**.

**Already configurable / generic:**

| Concern | Status |
|---------|--------|
| Endpoint / domain / bind | `FILEENGINE_LDAP_ENDPOINT` / `_DOMAIN` / `_BIND_DN` / `_BIND_PASSWORD` |
| User search base | `FILEENGINE_LDAP_USER_BASE` |
| Tenant base | `FILEENGINE_LDAP_TENANT_BASE` |
| Login identity | user matched by **`uid` OR `mail`** — covers `uid=email` and separate-uid+mail schemes |
| Tenant from group DN | derived **generically** as the `ou=` segment above the tenant base (`tenantFromGroupDN`) — works for any `tenant_base`, any tenant name |
| Group membership | `getTenantsForUser` matches **`member` / `uniqueMember` / `memberUid`** — covers `groupOfNames`, `groupOfUniqueNames`, and `posixGroup` |
| Role name | taken from the matched group's `cn` |
| Filter safety | login values are RFC-4515 escaped (LDAP-injection-safe) |

So **user base + tenant base + domain + bind is generally enough** to adapt to a
site's layout. The earlier idea of a full template/placeholder config system is
**not required**.

## 3. Potential improvements (cleanups, not a rewrite)

Concrete rough edges found in the code, in priority order:

1. **Remove example-specific hard-coded fallbacks** in
   `extractRolesFromGroups()`. It tries a list of `possible_bases` that includes
   a literal `ou=default,ou=tenants,<domain>` (with a comment naming the
   `rationalboxes` directory) plus `ou=groups` / `ou=Group` / `ou=Roles` /
   `ou=role` / `ou=users`. These produce the noisy `No such object` log errors
   and bake in the example layout. Should rely on the configured `tenant_base_`
   (as `getTenantsForUser` already does).
2. **Consolidate the two resolution paths.** Roles are resolved by
   `extractRolesFromGroups()` (objectClass=groupOfNames + `member` only, with the
   hard-coded base list) while tenants are resolved by `getTenantsForUser()`
   (generic, broader membership filter). They should share one generic routine so
   role/tenant resolution is consistent (and `posixGroup`/`uniqueMember` work for
   roles too, not just tenants).
3. **`extractTenantFromUserDN()`** assumes a tenant lives in the user's DN, but in
   this layout users sit under `ou=users` (no tenant component), so it yields
   nothing useful — the real tenants come from membership. Drop/relegate it to
   avoid a misleading "primary tenant".
4. **Optional config knobs** (only if a site needs them; sensible defaults today):
   group object class, member attribute, and the user login attribute — could
   become env-overridable, but are not blocking given the OR-based matching above.
5. **WebDAV tenant resolution** — resolved by design (**LDAP-2**): WebDAV is
   reached at `<tenant>-drive.<base>`, and the bridge takes the **first
   `-`-delimited segment** of the host label as the tenant (`someco-drive` →
   `someco`; fits the existing `extractTenantFromHost`). Tenant names therefore
   contain no hyphen.

These are quality/clarity improvements; the directory model itself is already
deployable via configuration.

---

## 3. Seeding the unified stack (389-ds)

The unified deployment uses 389 Directory Server; its seed LDIF reproduces this
same shape under the deployment's domain (defaults shipped, site-overridable):

```
dc=<domain>
├── ou=users                         # uid = email (inetOrgPerson)
└── ou=tenants
    └── ou=default
        ├── cn=users          (groupOfNames)
        ├── cn=contributors   (groupOfNames)
        ├── cn=administrators (groupOfNames)
        └── cn=system_admin   (groupOfNames)   # included in the seed (optional per site)
```

New tenant = new `ou=<tenant>,ou=tenants` + its role groups; the bridges resolve
it via the configured queries above with no code change. Use the helper
**`scripts/new-tenant.sh`** to create it:

```sh
# settings default to FILEENGINE_LDAP_* (the bridges' env); --container runs
# ldapadd inside the LDAP container when no client is installed locally.
scripts/new-tenant.sh acme --admin alice@example.com [--container ldap]
```

It adds the `ou=<tenant>` + `users`/`contributors`/`administrators`/`system_admin`
`groupOfNames` (idempotent; `--dry-run` prints the LDIF). The **core then creates
the tenant's DB schema and storage folder on first access** — no DB step needed.
