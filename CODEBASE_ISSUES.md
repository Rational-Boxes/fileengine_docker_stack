# Codebase Issues for the Unified Docker Deployment

Source-code / packaging changes required in the main repos to support the unified
`docker-compose` stack (see `SPECIFICATION.md` / `IMPLEMENTATION_PLAN.md`).

**Legend:** 🟥 blocker (deployment won't work correctly without it) · 🟧 important
(works but degraded/manual) · 🟦 enhancement/verification.

---

## file_engine_core

### CORE-1 ✅ — Build the core RPM with Redis events enabled (done)
`fileengine-core.spec` now builds with event emission enabled so the packaged
server can publish `fileengine:events` for automatic preview/rendition generation:
- `%build` cmake invocation gained **`-DFILEENGINE_ENABLE_EVENTS=ON`**.
- **`BuildRequires: hiredis-devel`** (so CMake's `find_library(hiredis)` succeeds
  rather than silently compiling events out with a warning).
- **`Requires: …, hiredis`** on `fileengine-libs` for the runtime link.
At runtime, set `FILEENGINE_EVENTS_ENABLED=true` + `FILEENGINE_REDIS_*` to turn
emission on (documented in `file_engine_core/CONFIGURATION.md`). Note: the Debian
(`debian/`) and Arch (`PKGBUILD`) packaging were **not** changed — the unified
stack uses the RPM; update those too if those package paths are ever used.

### CORE-2 ✅ — On-first-access tenant auto-provisioning (verified, default tenant)
Confirmed in Phase 4: with only the LDAP `default` tenant seeded (no explicit DB
provisioning), the first authenticated write created the file successfully — the
core materialized the `default` tenant's DB schema + storage folder on first
access. *(A brand-new non-default tenant via `scripts/new-tenant.sh` should be
spot-checked the same way, but the auto-provisioning path is proven.)*

### CORE-3 ✅ — Headless first-boot bootstrap against an empty DB (verified)
Confirmed in the Phase-1 smoke test: the events image, pointed at a brand-new
empty `fileengine` database, initialized the connection pool, **created/verified
the global tables, and verified the schema** with no manual SQL — then began
object-store init. No baseline SQL is required for `db-init` for the core's own
tables. *(Default-tenant provisioning on first access still tracked under CORE-2.)*

### CORE-4 ✅ — Container logging (verified)
Confirmed: with `FILEENGINE_LOG_TO_CONSOLE=true` / `FILEENGINE_LOG_TO_FILE=false`
(now baked as defaults in `images/core/Dockerfile`) the core logs to stdout and
is captured by `docker logs`.

### CORE-6 🟦 — Benign first-run StorageTracker error on a fresh tenant dir
On first boot against an empty `filecache` volume the core logs
`[ERROR] [StorageTracker] Error initializing from existing files: … cannot open
directory … /storage/default`, because the default tenant's storage dir doesn't
exist yet. It is **non-fatal** (the server continues and serves normally), but
StorageTracker should treat a missing per-tenant storage dir as empty (info/debug)
rather than logging an ERROR, to avoid false alarms in container logs.

### CORE-5 ✅ — Package the service user via sysusers.d (container-installable) (done)
The `fileengine-server` RPM ships files owned by user/group `fileengine`, so rpm
auto-generates `Requires: user(fileengine)`/`group(fileengine)` — but the account
was created only via `%pre useradd`, **providing neither symbol**, so a clean
`dnf install` in a minimal container failed with *"nothing provides
user(fileengine)"*. Fixed by shipping `/usr/lib/sysusers.d/fileengine.conf`
(`file_engine_core/fileengine.sysusers`, installed in the spec's `%install`/`%files`)
so the package **self-provides** those symbols and creates the account via
systemd-sysusers; the `%pre` useradd remains as a fallback. Verified: the events
RPM now installs cleanly into the base image (user auto-created, uid 998).

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

### LDAP-3 🟧 — Use a least-privilege LDAP bind account (not Directory Manager)
Phase 4 wires the bridges/MCP to bind as **`cn=Directory Manager`** (the 389-ds
root) using the instance DM password, which works but is over-privileged — the
services only need to **search** `ou=users`/`ou=tenants` and read group
membership. Add a dedicated read-only service account (e.g.
`cn=svc-bridge,<suffix>`) with an ACI granting read/search on those subtrees,
seed it in `ldap-init`, and point `LDAP_BIND_DN`/`LDAP_BIND_PASSWORD` at it so the
DM password isn't distributed to the app services.

### BRIDGE-1 ✅ — All wiring is env-configurable (verified)
Confirmed in Phase 3: both bridges read everything from env —
`FILEENGINE_GRPC_HOST/PORT`, `HTTP_HOST/PORT/MONITORING_PORT`, `WEBDAV_HOST/PORT/
MONITORING_PORT`, `HTTP_CORS_ORIGIN`, `HTTP_MAX_BODY_BYTES`, `HTTP_THREAD_POOL`,
the `FILEENGINE_LDAP_*` set, and `OAUTH_*`/`TOKEN_TTL_SECONDS`. No Postgres
(the PG keys in the repos' `.env` are vestigial — the bridges don't link libpq).
Compose wires them to service hostnames.

### BRIDGE-2 ✅ — Health endpoints & console logging (verified)
Both bridges expose `/healthz` `/readyz` `/poolz` on a monitoring listener
(http `:8091`, webdav `:8089`), and **http-bridge's main-port `/readyz` does a
real gRPC `ListDirectory` to the core** (verified `{"status":"ready"}`). Console
logging via `LOG_WRITE_TO_CONSOLE=true` captured by `docker logs`. *(The compose
healthchecks currently use a bash `/dev/tcp` liveness probe since the images have
no curl; switching them to `/readyz` would require adding curl — optional.)*

---

## convert_search_ai

### CSAI-1 ✅ — Independent embedder vs. LLM provider configuration (confirmed + hardened)
**Verified already independent and hardened.** The embedder and chat LLM are
configured by **separate** env groups (`CSAI_EMBEDDING_*` vs `CSAI_CHAT_*`),
resolved by **separate factory functions** with independent provider, model,
`*_BASE_URL`, and `*_API_KEY` — so the embedder can run on CPU-local Ollama while
chat targets an external provider, and switching one never requires the other.
Changes made:
- **Startup confirmation log** (`convert_search_ai/src/.../app.py`,
  `_log_ai_config`): on boot CSAI logs `AI providers — embeddings: provider/model/
  dim/endpoint | chat: provider/model/endpoint` (no secrets) so operators can
  confirm the split took effect — plus a `logging.basicConfig(level=INFO)` in
  `main()` so the banner is actually emitted under uvicorn (root defaults to WARNING).
- **Documented the split** as a first-class example in `convert_search_ai/.env.example`
  (CPU-local `nomic-embed-text` embeddings + external chat).
Verified in-stack (Phase 5): the worker ingested + **embedded via the bundled
Ollama `nomic-embed-text`** while chat was independently configured.

### CSAI-2 🟧 — Tolerate Ollama startup / model-pull latency
The one-shot **`ollama-init`** service pulls `nomic-embed-text` on first startup
(see IMPLEMENTATION_PLAN §11), and CSAI `depends_on` its completion (verified: the
model was pulled before the workers started). CSAI should still **retry/degrade
gracefully** if Ollama is briefly unavailable or warming up rather than failing
conversions/queries permanently — remaining hardening for a future pass.

### CSAI-3 ✅ — Health endpoint (verified)
CSAI exposes `GET /healthz` (and `/readyz`). Used as the `csai-app` compose
healthcheck (via a small `python3 urllib` probe, since the image has no curl).

### CSAI-5 ✅ — Worker crash-loop on blocking-read timeout (fixed)
Bringing the ingest worker up as a long-running service, a blocking `XREADGROUP`
(`block_ms=5000`) that returned no entries surfaced as
`redis.exceptions.TimeoutError: Timeout reading from socket` (redis-py/RESP3 sets
the read timeout to ~`block_ms` with no buffer), **crash-looping** the worker so
it never processed events. Fixed in `events.py` `RedisEventSource.read()` —
catch the timeout and return an empty batch (no events) so the poll loop continues.

### CSAI-6 ✅ — On-demand tenant provisioning in the worker (fixed)
The worker queried `documents`/`chunks` before the tenant's CSAI schema existed
(`relation "documents" does not exist`) — the per-tenant schema is created **on
demand by code**, but only the on-demand convert endpoint did so, not the
event-driven worker. Fixed in `ingest.py`: provision the tenant schema on its
first event (idempotent, cached), mirroring the convert endpoint. *(Edge: search
on a never-ingested tenant still 500s until first ingest provisions it — a
read-path `ensure_provisioned`/empty-result guard is a small follow-up.)*

### CSAI-4 🟧 — Install the full conversion toolchain (no silent degradation)
CSAI **degrades silently** when a conversion dependency is missing —
`tools.have(...)` guards, lazy imports, and the PDF-backend chain
(`docling → pymupdf4llm → pdfplumber → pdftotext`) all fall through, so a partial
install quietly drops preview/extraction fidelity rather than erroring. The
`fileengine-csai` image must install the **full set** (see IMPLEMENTATION_PLAN §3.1):
- **System tools:** LibreOffice, poppler-utils (`pdftoppm`+`pdftotext`), ImageMagick,
  ffmpeg (with **libopenh264**+**libvpx** encoders), libmagic.
- **Python backends:** `convert_search_ai[pdf,pdf-docling,pdf-pymupdf]`.
Two deliberate caveats the operator may weigh: **docling** pulls ML models (image
size), and **pymupdf4llm/PyMuPDF is AGPL-3.0** (license-sensitive sites may omit
that one extra — the chain still works via docling/pdfplumber/pdftotext). Everything
else ships by default. *(Image/packaging concern, not a code change.)*

---

## fileengine-mcp

### MCP-1 🟦 — Health/readiness endpoint
The MCP HTTP server has no dedicated health endpoint; `/mcp` and `/whoami` return
401 unauthenticated. For a clean compose healthcheck, add a small unauthenticated
`/healthz` (or `/readyz` that reports core-gRPC + LDAP reachability). Until then
the healthcheck is a TCP/HTTP liveness probe on `:8089`.

### MCP-2 ✅ — Tenant-from-Host behind the reverse proxy + clean path prefix (verified)
**Verified in Phase 6:** nginx routes `location = /mcp` → `mcp:8089/mcp`
(Streamable-HTTP) and `location /mcp/` → `mcp:8089/` (helpers `/mcp/auth/token`,
`/mcp/whoami`), passing the Host through (`proxy_set_header Host $host`). A
`GET <tenant>.<base>/mcp/whoami` returned the identity with the **tenant resolved
from the Host subdomain** — no `X-Tenant` needed. The dedicated `<tenant>-mcp.<base>`
subdomain was deliberately not used (MCP takes the whole first label, so it would
mis-resolve `someco-mcp`; the path avoids it). Optional polish: honor
`X-Forwarded-Prefix` so the helper paths are prefix-clean.

### MCP-3 🟦 — Tool-exposure policy defaults for the deployment
Confirm the deployment sets a sensible policy via `MCP_*`: writes on,
**delete off** (`MCP_ALLOW_DELETE=0`, the default), per-call size caps
(`MCP_MAX_READ_BYTES`/`MCP_MAX_WRITE_BYTES`/`MCP_MAX_RESULTS`), and optionally
`MCP_READ_ONLY=1` or an `MCP_SUBTREE_ALLOWLIST` sandbox for untrusted agents.

---

## frontend

### FE-1 ✅ — Same-origin / path-prefixed reverse-proxy operation (verified through nginx)
The SPA supports same-origin path bases behind nginx — **`.env.production`** sets
`VITE_API_BASE=/api` and `VITE_CSAI_BASE=/csai`, and `csaiClient.chatSocketUrl()`
resolves a **relative** base against `window.location` (picking `ws`/`wss`) so the
chat WebSocket works on `/csai` (nginx carries the Upgrade/Connection headers).
**Verified in Phase 6 through the real nginx proxy:** the SPA is served at
`<tenant>.<base>`, `/api` login→whoami→**streaming upload (204)**, then `/csai`
search returned the freshly-ingested doc — the whole same-origin flow. *(Remaining
spot-checks for completeness: large blob **Range** requests for PDF/video inline
preview; `nginx` sets `proxy_buffering off` on `/api` + `/csai` for streaming.)*

### FE-2 ✅ — SPA: select the active tenant from the subdomain (done)
The SPA derives the active tenant from the hostname. New
`frontend/src/utils/tenantHost.ts` parses `<tenant>.<base>` using
**`VITE_BASE_DOMAIN`**; `auth.initTenantFromHost()` (called first in `App.vue`
bootstrap) adopts it as the active tenant — set **before** `whoami()` so the
`X-Tenant` header and tenant listing are scoped correctly — overriding any
persisted selection. The apex / non-tenant host (and `localhost` dev, where
`VITE_BASE_DOMAIN` is empty) falls back to the persisted/selected tenant. The
`TenantSelector` now **navigates to the chosen tenant's subdomain** (each tenant
is its own origin) when subdomain tenancy is enabled, else does the in-app swap.
Reserved labels (`www`/`app`/`api`/`csai`) are ignored. Optional follow-up: the
http-bridge may validate that a request's `X-Tenant` matches the host subdomain.

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
