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
  confirm the split took effect.
- **Documented the split** as a first-class example in `convert_search_ai/.env.example`
  (CPU-local `nomic-embed-text` embeddings + external chat).

### CSAI-2 🟧 — Tolerate Ollama startup / model-pull latency
The one-shot **`ollama-init`** service pulls `nomic-embed-text` on first startup
(see IMPLEMENTATION_PLAN §11), and CSAI `depends_on` its completion — but CSAI
(app + worker) should still **retry/degrade gracefully** if Ollama is briefly
unavailable or warming up, rather than failing conversions/queries permanently.

### CSAI-3 🟦 — Health endpoint
Confirm CSAI exposes a `/health` (or similar) endpoint for the compose
healthcheck.

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

### MCP-2 🟧 — Tenant-from-Host behind the reverse proxy + clean path prefix
MCP is served at `<tenant>.<base>/mcp` and resolves the tenant from the Host
subdomain's **first label** (or an explicit `X-Tenant`), so nginx must
**pass the original Host through** (`proxy_set_header Host $host`) for tenancy to
work. Two things to confirm/handle when finalizing the vhost:
- the Streamable-HTTP endpoint is reachable cleanly at `/mcp` while its helper
  routes (`/auth/token`, `/whoami`) sit under the same prefix — map locations so
  the public paths are unambiguous (consider honoring `X-Forwarded-Prefix`);
- MCP's `extract_tenant` takes the **whole** first label, so a dedicated
  `<tenant>-mcp.<base>` subdomain would mis-resolve (`someco-mcp`) — that's why the
  **path** `/mcp` was chosen; if a dedicated MCP subdomain is ever wanted, MCP must
  hyphen-split the host like the WebDAV bridge (LDAP-2).

### MCP-3 🟦 — Tool-exposure policy defaults for the deployment
Confirm the deployment sets a sensible policy via `MCP_*`: writes on,
**delete off** (`MCP_ALLOW_DELETE=0`, the default), per-call size caps
(`MCP_MAX_READ_BYTES`/`MCP_MAX_WRITE_BYTES`/`MCP_MAX_RESULTS`), and optionally
`MCP_READ_ONLY=1` or an `MCP_SUBTREE_ALLOWLIST` sandbox for untrusted agents.

---

## frontend

### FE-1 🟧 — Same-origin / path-prefixed reverse-proxy operation (wired; flows to verify)
**Done:** the SPA now supports same-origin path bases behind nginx —
**`.env.production`** sets `VITE_API_BASE=/api` and `VITE_CSAI_BASE=/csai`, and
`csaiClient.chatSocketUrl()` resolves a **relative** base against
`window.location` (picking `ws`/`wss` from the page) so the chat WebSocket works
on `/csai`. The api/auth clients already use `${BASE}/v1/...` (relative-safe) and
OAuth `return_to` already builds from `window.location.origin` (public origin, no
hard-coded localhost). **Still to verify end-to-end behind the real proxy:**
- blob downloads and **Range requests** (PDF/video inline preview),
- chunked/streaming upload + download through `/api`,
- WebDAV-served content if referenced.

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
