# Codebase Issues for the Unified Docker Deployment

Source-code / packaging changes required in the main repos to support the unified
`docker-compose` stack (see `SPECIFICATION.md` / `IMPLEMENTATION_PLAN.md`).

**Legend:** 🟥 blocker (deployment won't work correctly without it) · 🟧 important
(works but degraded/manual) · 🟦 enhancement/verification.

---

## file_engine_core

### CORE-1 🟥 — Build the core RPM with Redis events enabled
`fileengine-core.spec`'s `%build` does **not** pass `-DFILEENGINE_ENABLE_EVENTS=ON`,
so the packaged binary has event emission compiled out (no `hiredis` link). The
unified stack relies on the core publishing `fileengine:events` for automatic
preview/rendition generation.
- Add an events-enabled build: `-DFILEENGINE_ENABLE_EVENTS=ON`,
  `BuildRequires: hiredis-devel`, `Requires: hiredis` — either as the default or a
  dedicated build flag/sub-package the image build selects.
- (Already documented in `file_engine_core/CONFIGURATION.md`.)

### CORE-2 🟦 — Verify on-first-access tenant auto-provisioning
By design, a new tenant is created in LDAP (`scripts/new-tenant.sh`) and the core
**auto-creates the tenant's DB schema and storage folder on first access** — no
explicit provisioning command is required. Verify this works end-to-end for a
brand-new tenant in the containerized deployment (schema + folder materialize on
the first request without manual steps). No new admin command needed.

### CORE-3 🟦 — Headless first-boot bootstrap against an empty DB
Confirm the core initializes cleanly against a brand-new empty `fileengine`
database (TenantManager schema creation, default tenant) with no manual SQL.
Provide any required baseline SQL for the `db-init` job if not fully automatic.

### CORE-4 🟦 — Container logging
Confirm/standardize stdout logging in containers (`FILEENGINE_LOG_TO_CONSOLE=true`,
`FILEENGINE_LOG_TO_FILE=false`) so logs are captured by Docker/Podman.

---

## http_bridge & webdav_bridge (shared LDAP authenticator)

### LDAP-1 🟧 — Clean up example-specific LDAP fallbacks; consolidate role/tenant resolution
**Re-scoped after reviewing the code** (`http_bridge/src/ldap_authenticator.cpp`):
the authenticator is **already substantially site-configurable** — `USER_BASE`,
`TENANT_BASE`, `DOMAIN`, `BIND_*` are env fields; user lookup matches `uid` OR
`mail`; tenant is derived generically from each group DN relative to
`TENANT_BASE`; and membership is matched across `member`/`uniqueMember`/
`memberUid`. **Deployment-specific config is largely covered** — no
template/placeholder system is needed. See `LDAP_REFERENCE.md` §2–3.

Remaining cleanups (not blocking):
- **Remove the hard-coded example fallbacks** in `extractRolesFromGroups()`
  (literal `ou=default,ou=tenants,<domain>` referencing the `rationalboxes`
  example, plus `ou=groups`/`ou=Group`/`ou=Roles`/`ou=role`/`ou=users`) — these
  cause the noisy `No such object` logs and bake in the example layout. Rely on
  the configured `TENANT_BASE`.
- **Consolidate** the two parallel resolution paths (`extractRolesFromGroups`
  vs. `getTenantsForUser`) into one generic routine so roles also benefit from
  the broader membership matching.
- **Retire `extractTenantFromUserDN()`** (users live under `ou=users` with no
  tenant in their DN; the real tenants come from membership).
- *(Optional)* expose group object-class / member-attr / login-attr as env knobs.

Factor into the **shared** authenticator used by both bridges.

### LDAP-2 🟧 — WebDAV tenant from the `-drive` subdomain
**Resolved by design:** WebDAV is always reached at `<tenant>-drive.<base>`, so
`webdav-bridge` derives the tenant from the Host by **splitting the first label
on `-` and taking the first segment** (`someco-drive.<base>` → `someco`).
Implication: **tenant names must not contain a hyphen** (enforced by
`new-tenant.sh`) — which is consistent with Postgres schema-name restrictions, so
the same constraint already applies to the auto-created `tenant_<tenant>` schema.
This fits the existing `extractTenantFromHost`. Validate the resolved tenant
against the authenticated user's LDAP membership.

### BRIDGE-1 🟦 — Confirm all wiring is env-configurable
Verify core gRPC address/port, listen ports, `HTTP_CORS_ORIGIN` /
`CSAI`-facing CORS, `HTTP_MAX_BODY_BYTES`, LDAP server URL, and bind credentials
are all settable via env (for compose service hostnames). Most already are;
confirm and fill any gaps.

### BRIDGE-2 🟦 — Health endpoints & console logging
`http-bridge` exposes `/healthz` / `/readyz`. Confirm `webdav-bridge` exposes a
health endpoint for the compose healthcheck (add if missing), and that both log
to stdout.

---

## convert_search_ai

### CSAI-1 🟧 — Independent embedder vs. LLM provider configuration
Per the decision to bundle Ollama now but "revisit the service to point the
embedder and LLM to different providers": verify and, if needed, harden that the
embedder and the chat LLM can target **different providers/endpoints
independently** (today: `CSAI_EMBEDDING_*` vs `CSAI_CHAT_*`). Document the matrix
and ensure switching one doesn't require the other.

### CSAI-2 🟧 — Tolerate Ollama startup / model-pull latency
The bundled `ollama` service pulls `nomic-embed-text` on first run. CSAI (app +
worker) should **retry/degrade gracefully** while Ollama is unavailable or the
model isn't pulled yet, rather than failing conversions/queries permanently.

### CSAI-3 🟦 — Health endpoint
Confirm CSAI exposes a `/health` (or similar) endpoint for the compose
healthcheck.

---

## frontend

### FE-1 🟥 — Verify same-origin / path-prefixed reverse-proxy operation
The SPA must work built with `VITE_API_BASE=/api` and `VITE_CSAI_BASE=/csai`
(relative, same-origin) behind nginx. Verify the flows that touch absolute URLs
or special transfer modes:
- blob downloads and **Range requests** (PDF/video inline preview),
- chunked/streaming upload + download through `/api`,
- WebDAV-served content if referenced,
- OAuth `return_to` URL construction (must resolve to the public origin, not a
  hard-coded `localhost`).

### FE-2 🟧 — SPA: select the active tenant from the subdomain
The SPA is served per-tenant at `<tenant>.<base>`. On load it must **derive the
active tenant from the hostname** (the subdomain label) and set it as the active
tenant / `X-Tenant`, rather than relying solely on the in-app selector. A user
with access to multiple tenants switches by visiting another tenant's subdomain;
the selector stays for convenience but defaults to the subdomain's tenant. Handle
the apex / non-tenant host (redirect to a default tenant or a chooser). The
http-bridge may optionally validate that a request's `X-Tenant` matches the host
subdomain.

---

## Cross-cutting

### X-1 🟧 — Runnable migration/baseline artifacts for `db-init`
Ensure the `db-init` job has standalone, idempotent artifacts: CSAI's
`migrations/0001_baseline.sql` + pgvector `CREATE EXTENSION`, plus whatever the
core needs for an empty DB (see CORE-3), and default-tenant provisioning
(ties to CORE-2).

### X-2 ✅ — End-to-end "new tenant" procedure (delivered)
`scripts/new-tenant.sh` provisions the LDAP side (`ou=<tenant>` + role groups,
idempotent, `--dry-run`); the core auto-creates the DB schema + storage folder on
first access (CORE-2). Tested against the dev directory. Just needs a one-line
mention in the operator README.
