# Unified FileEngine Stack — Implementation Plan

> Status: **draft for review.** This plan turns `SPECIFICATION.md` into a concrete,
> buildable design. It reflects the decisions made during review (see
> [§13 Decisions log](#13-decisions-log)). Items still needing confirmation are
> marked **(confirm)**.

---

## 1. Goals & non-goals

**Goal:** a unified `docker-compose` deployment of the entire FileEngine stack
that runs on conventional Docker- or Podman-based cloud hosting, needs only an
**external S3-compatible object store** and DNS records pointing the domain at
the host IP, and obtains TLS automatically via Let's Encrypt.

**Non-goals (this phase):**
- A single all-in-one image (explicitly rejected — this is a multi-service compose stack).
- Bundling the object store (S3 is external; no MinIO in the default compose).
- Bundling the AI model server (the AI provider is external/configurable).
- Kubernetes manifests, autoscaling, HA/replication (single-host compose for now).

---

## 2. Topology

```
                              Internet
                                 │  :80 (ACME + redirect)  :443 (TLS)
                                 ▼
                        ┌──────────────────┐
                        │      nginx       │  TLS termination (LE wildcard *.base,
                        │  SPA + reverse   │  DNS-01); routes by tenant subdomain:
                        │  proxy + certbot │  <t>.base → SPA+/api+/csai+/mcp;
                        └──────────────────┘  <t>-drive.base → webdav
   <t>.base /api │  /csai │  /mcp │   <t>-drive.base │   <t>.base / (SPA)
                 ▼        ▼       ▼              ▼
        ┌─────────────┐ ┌──────────┐ ┌───────┐ ┌────────────────┐
        │ http-bridge │ │ csai-app │ │  mcp  │ │ webdav-bridge  │
        └─────────────┘ └──────────┘ └───────┘ └────────────────┘
                 │       gRPC :50051 (internal only)     │
                 └───────────────┬──────────────────────┘
                                 ▼   (mcp also → core gRPC + LDAP)
                          ┌─────────────┐        ┌──────────────┐
                          │    core     │◀──────▶│  csai-worker │ (ingest)
                          │  (gRPC)     │  events│              │
                          └─────────────┘        └──────────────┘
        internal backing services (compose network only):        external:
        ┌──────────┐  ┌────────┐  ┌──────────┐  ┌────────┐   ┌──────────────┐
        │ postgres │  │ redis  │  │ 389-ds   │  │ ollama │   │  S3 bucket   │
        │ +pgvector│  │ (auth) │  │ (LDAP +  │  │ (CPU,  │   └──────────────┘
        └──────────┘  └────────┘  │ console) │  │ embed) │
                                  └──────────┘  └────────┘
```

Only **nginx** publishes ports (`80`, `443`). Everything else communicates over
the internal compose network; the **core gRPC port is never exposed** (trusted-
access model: the bridges are the only callers and they pass a trusted
`AuthenticationContext`).

---

## 3. Services & images

Each FileEngine process is its **own image** (per decision). All FileEngine
images share a common Fedora base layer with the provided AWS SDK RPMs installed.

| Service         | Image                     | Base / contents                                                                 | Listens (internal) |
|-----------------|---------------------------|----------------------------------------------------------------------------------|--------------------|
| `core`          | `fileengine-core`         | Fedora + AWS SDK RPMs + `fileengine-server`/`fileengine-libs` RPMs (events ON)    | gRPC `50051`, metrics `8081` |
| `http-bridge`   | `fileengine-http-bridge`  | Fedora + `fileengine-http-bridge` RPM                                             | `8090` |
| `webdav-bridge` | `fileengine-webdav-bridge`| Fedora + `fileengine-webdav-bridge` RPM                                           | `8088` |
| `csai-app`      | `fileengine-csai`         | Fedora + Python + `convert_search_ai` + conversion tools                          | `8092` |
| `csai-worker`   | `fileengine-csai`         | (same image as `csai-app`, command = ingest worker)                              | — |
| `mcp`           | `fileengine-mcp`          | Python + `python_interface` client + `fileengine-mcp` (Streamable-HTTP MCP server for AI agents) | `8089` |
| `nginx`         | `fileengine-nginx`        | nginx + certbot + **built SPA** + rendered vhost config                          | `80`, `443` |
| `postgres`      | `pgvector/pgvector:pg16`  | Postgres 16 with the `vector` extension (CSAI requires it)                       | `5432` |
| `redis`         | `redis:7`                 | password-protected (core events + CSAI)                                          | `6379` |
| `ldap`          | `389ds/dirsrv`            | **389 Directory Server** + integrated Cockpit web console; seeded uid=email + tenants OU — see §8 | `3389`/`3636`, console `9090` |
| `ollama`        | `ollama/ollama`           | bundled CPU AI; pulls the embedding model (`nomic-embed-text`); CSAI points here  | `11434` |

> `csai-app` and `csai-worker` are **two services from one image**, differing only
> in `command` (FastAPI app vs. `python -m convert_search_ai.ingest`).
>
> `mcp` runs the **HTTP transport** (`fileengine-mcp-http`, binds `:8089`), not the
> stdio variant — so it is a long-lived service reachable through nginx. Like CSAI
> it reaches the **core over gRPC** and authenticates against **LDAP** per request;
> it needs no Postgres/Redis. It is **already tenant-aware** (resolves the tenant
> from an explicit `X-Tenant` header, else the Host subdomain's first label).

### 3.1 Conversion tooling (in `fileengine-csai`)

Renditions/previews and PDF→Markdown extraction need **both system tools and
Python backends**. CSAI **degrades silently** when a dependency is missing
(`tools.have(...)` guards, lazy imports, and the PDF-backend chain falls through),
so a partial install quietly drops fidelity rather than erroring. The image
therefore installs the **full set** so nothing degrades unnoticed.

**System packages** (invoked via `subprocess`):

| Tool (pkg) | Used for |
|------------|----------|
| **LibreOffice** (`libreoffice`/`soffice`) | office (doc/xls/ppt…) → PDF |
| **poppler-utils** (`pdftoppm`, `pdftotext`) | PDF → page-1 preview PNG; last-resort PDF text |
| **ImageMagick** (`magick`/`convert`) | image thumbnails |
| **ffmpeg** (with **libopenh264** + **libvpx**) | video poster frame + WebM/VP9 preview clip (the `ffmpeg-free` build ships libopenh264, not libx264 — CSAI picks the available encoder) |
| **libmagic** (`file`/`python-magic`) | MIME sniffing |

**Python PDF→Markdown backends** — CSAI installs with the conversion extras so
the full fidelity-ordered chain works (`DEFAULT_ORDER = docling, pymupdf4llm,
pdfplumber, pdftotext`):

| Extra | Package | Notes |
|-------|---------|-------|
| `pdf` | `pdfplumber` | MIT, light — solid tables (baseline) |
| `pdf-docling` | `docling` | MIT, **heavy (ML models)** — best structure/tables; largest image cost |
| `pdf-pymupdf` | `pymupdf4llm` | **PyMuPDF is AGPL-3.0** — license-sensitive deployments may omit this extra (the chain still works via docling/pdfplumber/pdftotext) |

> Build installs `convert_search_ai[pdf,pdf-docling,pdf-pymupdf]` plus the AI
> provider extras a deployment uses (`anthropic`/`openai`/`voyage`). The two
> caveats — **docling's ML download/size** and **pymupdf4llm's AGPL license** —
> are the only reasons to trim the set; everything else is installed by default so
> previews/extraction never silently downgrade. These add significant image size
> but are otherwise mandatory for the preview/extraction pipeline.

---

## 4. Build pipeline

The compose build depends on artifacts produced from the source repos. The plan
adds a **build/prepare step** (a `make`-driven script in `docker_unified/`) that:

1. **Builds the FileEngine RPMs** from the source repos and drops them into
   `rpms/fileengine/`:
   - `file_engine_core` → `fileengine-server`, `fileengine-libs`, `fileengine-cli`
     **built with `-DFILEENGINE_ENABLE_EVENTS=ON`** (+ `hiredis`), so the packaged
     core can emit Redis events (auto-previews depend on this — the stock spec
     defaults events OFF; the image build must override it).
   - `http_bridge` → `fileengine-http-bridge` RPM.
   - `webdav_bridge` → `fileengine-webdav-bridge` RPM.
2. **Builds the SPA**: `frontend` → `npm ci && npm run build` with
   `VITE_API_BASE=/api` and `VITE_CSAI_BASE=/csai` (same-origin), output copied
   into the `fileengine-nginx` build context.
3. **Stages `convert_search_ai`** source (or a wheel) for the CSAI image; the
   `fileengine-csai` Dockerfile installs it **with the conversion extras**
   (`convert_search_ai[pdf,pdf-docling,pdf-pymupdf]` + chosen AI-provider extras)
   and **all conversion system tools** (LibreOffice, poppler-utils, ImageMagick,
   ffmpeg+libopenh264/libvpx, libmagic) — see §3.1.
4. **Stages `mcp` + `python_interface`** for the `fileengine-mcp` image. The MCP
   server depends on the `fileengine` Python client in `python_interface/`, so its
   build context must include **both** repos (the existing `mcp/Dockerfile` builds
   from the parent dir and `pip install`s `python_interface` then `mcp`). Pure
   Python — no RPM.
5. AWS SDK RPMs are already provided under `rpms/aws-sdk/` (v1.11.725).

Each `Dockerfile` then `dnf install`s the relevant RPMs from the local `rpms/`
directory. (Per decision, binaries come from **prebuilt RPMs**, not in-image
source compilation.)

---

## 5. Reverse proxy & routing (nginx) — per-tenant subdomains

Two vhosts under the wildcard cert, keyed on the Host:

| Host pattern | Routing |
|--------------|---------|
| `<tenant>-drive.<base>` | → `webdav-bridge:8088` (WebDAV); the bridge resolves the tenant as the **first `-`-delimited segment** of the host label (`someco-drive` → `someco`) |
| `*.<base>` (any other `<tenant>.<base>`) | `/` → SPA static (`try_files … /index.html`); `/api/` → `http-bridge:8090`; `/csai/` → `csai-app:8092`; `/mcp` → `mcp:8089` |

nginx server blocks:
- a vhost matching `*-drive.<base>` → WebDAV (Host passed through; the bridge
  takes the **first `-`-delimited segment** of the host label as the tenant),
- a wildcard vhost `*.<base>` → SPA + same-origin `/api` + `/csai`.

Tenant names contain **no hyphen**, so an SPA host (`<tenant>.<base>`, no hyphen
in the label) never collides with a WebDAV host (`<tenant>-drive.<base>`).

**MCP** is served at **`<tenant>.<base>/mcp`** (the Streamable-HTTP endpoint), with
its helper routes (`/auth/token`, `/whoami`) under the same prefix; nginx passes
the Host through so the MCP server resolves the tenant from the subdomain (or an
explicit `X-Tenant`) — no hyphen-split needed, since the tenant label is bare. A
dedicated `<tenant>-mcp.<base>` subdomain was **not** chosen: it would require the
MCP server to hyphen-split the host like WebDAV (it currently takes the whole
first label), whereas the same-origin `/mcp` path reuses the existing routing and
MCP's existing tenant logic unchanged.

Key settings:
- `client_max_body_size` large + `proxy_request_buffering off` /
  `proxy_buffering off` on upload/download paths (preserve end-to-end streaming).
- `:80` → redirect to `:443`.
- The **SPA reads its `<tenant>.<base>` subdomain to set the active tenant**
  (FE-2). `/api`+`/csai` are same-origin within the subdomain, so CORS stays
  minimal (only extra origins declared in `.env`).

---

## 6. Domain & TLS configuration — manual and API-automated options

Per-tenant subdomains need a **wildcard cert** (HTTP-01 can't issue wildcards, so
DNS-01 or BYO). Both **manual** and **API-automated** paths are first-class,
selected by **`TLS_MODE`**:

| `TLS_MODE` | How the `*.<base>` cert is obtained | DNS API creds? |
|------------|-------------------------------------|----------------|
| `letsencrypt-dns` *(default — automated)* | certbot's **DNS plugin** uses the provider **API** to answer the DNS-01 challenge and **auto-renew**, unattended | yes (`LE_DNS_PROVIDER` + token/keys) |
| `letsencrypt-manual` *(manual DNS-01)* | certbot prints the `_acme-challenge` **TXT record**; the operator adds it at any DNS provider. Interactive issuance; renewals are re-run (documented reminder/hook) | no |
| `byo` *(manual cert)* | operator drops a **wildcard cert/key** in the mounted TLS folder (commercial / internal CA / DNS-01 done elsewhere); no ACME in the stack | no |

- In all modes the cert lives on the mounted **`/etc/letsencrypt`** (or BYO)
  volume; a **self-signed bootstrap** lets nginx start before a cert exists.
- Inputs: `BASE_DOMAIN`, `TLS_MODE`, `LE_EMAIL`, `LE_STAGING`, and (for
  `letsencrypt-dns`) `LE_DNS_PROVIDER` + provider creds.

**DNS records** likewise support both: a single **wildcard A record
`*.<base> → host IP`** covers every `<tenant>.<base>` and `<tenant>-drive.<base>`,
so tenant onboarding needs **no DNS change**. That record can be created
**manually** (one-time) or via the provider **API**; per-tenant DNS automation is
only needed if a wildcard A record isn't used.

---

## 7. Data services

### 7.1 Postgres (`pgvector/pgvector:pg16`)
- One instance hosting **two databases**:
  - `fileengine` — core data; per-tenant schemas (`tenant_<name>`).
  - `convert_search_ai` — CSAI; pgvector columns (embedding dim must match the
    embedding model, e.g. **768** for `nomic-embed-text`).
- **Init job** (one-shot compose service, runs after Postgres is healthy):
  1. `CREATE DATABASE` for both (if absent).
  2. `CREATE EXTENSION vector` in `convert_search_ai`.
  3. Apply core baseline schema + CSAI baseline migration.
  4. Provision the **default tenant** (core schema + CSAI tenant schema at the
     configured embedding dimension).
- Data persisted to a named volume.

### 7.2 Redis (`redis:7`)
- Password-protected (`--requirepass`). Used by the core for the
  `fileengine:events` stream (drives CSAI ingest/previews) and by CSAI's
  permission-cache invalidator. Persisted to a named volume (optional).

### 7.3 LDAP (389 Directory Server) — see §8.

---

## 8. LDAP directory model

`uid = email`. Tenants are modeled as OUs of role groups; membership defines a
user's tenants and roles.

```
dc=<domain>                         # from BASE_DOMAIN (e.g. dc=example,dc=com)
├── ou=people                       # person entries; uid = full email
│   └── uid=admin@<domain>          # seeded initial admin (creds from .env)
└── ou=tenants
    └── ou=default                  # one OU per tenant
        ├── cn=system_admin   (groupOfNames)   # member: uid=admin@…,ou=people,…
        ├── cn=administrators (groupOfNames)
        ├── cn=contributors   (groupOfNames)
        └── cn=users          (groupOfNames)
```

**Resolution at login (membership-based):** the bridge binds/looks up the user
in `ou=people`, then derives **(tenant, roles)** from the user's `groupOfNames`
memberships under `ou=<tenant>,ou=tenants`. A user may belong to **multiple
tenants** by being a member of role groups in multiple tenant OUs.

**Role → access-level mapping (reuse current roles):**

| Group (`cn`)      | App role         | Access level |
|-------------------|------------------|--------------|
| `users`           | `users`          | user         |
| `contributors`    | `contributors`   | editor       |
| `administrators`  | `administrators` | admin        |
| `system_admin`    | `system_admin`   | admin        |

**Seeding (from `.env`):** create `dc=<domain>`, `ou=people`, `ou=tenants`,
`ou=default`, the four role groups, and the initial admin user
(`LDAP_ADMIN_EMAIL` / `LDAP_ADMIN_PASSWORD`) as a member of `cn=system_admin` and
`cn=administrators` in `ou=default`.

**New-tenant procedure (documented for operators):** add
`ou=<tenant>,ou=tenants`, create the four role `groupOfNames`, add members. The
core/CSAI provision the tenant's DB schema on first access (or via a provided
admin command).

> **Integration status:** the authenticator is **already largely site-configurable**
> (`USER_BASE`/`TENANT_BASE`/`DOMAIN`/`BIND_*` env; `uid`|`mail` lookup; generic
> membership relative to `TENANT_BASE`). **Active-tenant selection is host-based:**
> the SPA derives it from its `<tenant>.<base>` subdomain (**FE-2**), and the
> **WebDAV bridge from the `<tenant>-drive.<base>` host** by taking the first
> `-`-delimited label segment (resolves **LDAP-2**; fits the existing
> `extractTenantFromHost`; tenant names contain no hyphen). Remaining
> source work is the **LDAP-1** cleanup. Full assessment + reference DIT in
> `LDAP_REFERENCE.md` (reference uses `ou=users` — configurable). Stack runs
> **389-ds** (`389ds/dirsrv`, Cockpit console), seeded via LDIF + `dsconf`.

---

## 9. Configuration

All configuration is **operator-provided** (no auto-generation of secrets).

- **`docker_unified/.env`** — the single source of secrets/tunables, consumed by
  `docker-compose` and propagated to services. Provided from
  `.env.example` (to be written).
- A small **config-render init step** translates the high-level inputs
  (`BASE_DOMAIN`, `EXTRA_CORS_ORIGINS`, DNS-01 creds, …) into the nginx wildcard
  vhosts and the bridges'/CSAI's CORS allow-lists.

> **Decided:** configured via **`.env` variables** — `BASE_DOMAIN` (tenant hosts
> `<tenant>.<base>` and `<tenant>-drive.<base>` derive from it), `EXTRA_CORS_ORIGINS`,
> and the DNS-01 provider credentials — no separate `domains.yaml`.

### 9.1 `.env` reference (initial)

| Variable | Used by | Purpose |
|----------|---------|---------|
| `BASE_DOMAIN` | nginx, TLS, LDAP | base domain (e.g. `host.com`); tenant hosts `<tenant>.<base>` + `<tenant>-drive.<base>`; LDAP base DN derives from it |
| `EXTRA_CORS_ORIGINS` | http-bridge, csai | extra allowed origins beyond same-origin |
| `TLS_MODE` | nginx/certbot | `letsencrypt-dns` (API, default) \| `letsencrypt-manual` \| `byo` |
| `LE_EMAIL`, `LE_STAGING` | certbot | ACME registration email + staging-CA toggle |
| `LE_DNS_PROVIDER` + provider API creds | certbot | DNS-01 **automated** mode only (e.g. `cloudflare`+token, `route53`+keys) |
| `S3_ENDPOINT`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_PATH_STYLE` | core | external object store |
| `AT_REST_KEY` | core | at-rest encryption key — **must be stable/persisted; losing it makes stored data unreadable** |
| `FILEENGINE_ENCRYPT_DATA`, `FILEENGINE_COMPRESS_DATA` | core | at-rest encryption/compression toggles |
| `POSTGRES_USER`, `POSTGRES_PASSWORD` | postgres, core, csai | DB superuser/app creds |
| `CORE_DB`=`fileengine`, `CSAI_DB`=`convert_search_ai` | init, core, csai | database names |
| `REDIS_PASSWORD` | redis, core, csai | event bus auth |
| `LDAP_ADMIN_EMAIL`, `LDAP_ADMIN_PASSWORD` | ldap seed | initial administrative user |
| `LDAP_ROOT_PASSWORD` | ldap | directory manager (cn=Directory Manager) password |
| `CSAI_AGENT_EMAIL`, `CSAI_AGENT_PASSWORD` | csai | LDAP agent identity that writes renditions/indexes |
| `CSAI_EMBEDDING_PROVIDER/_MODEL/_BASE_URL/_API_KEY/_DIMENSION` | csai | embeddings (external/offline) |
| `CSAI_CHAT_PROVIDER/_MODEL/_BASE_URL/_API_KEY` | csai | chat/RAG (external/offline) |
| `MCP_AGENT_EMAIL`, `MCP_AGENT_PASSWORD` | mcp | LDAP agent identity for the stdio fallback / service bind (per-request callers auth with their own creds) |
| `MCP_READ_ONLY`, `MCP_ALLOW_DELETE` | mcp | tool-exposure policy (default: writes on, delete **off**) |
| `MCP_MAX_READ_BYTES`, `MCP_MAX_WRITE_BYTES`, `MCP_MAX_RESULTS` | mcp | per-call guardrails |
| `MCP_SUBTREE_ALLOWLIST`, `MCP_TOKEN_TTL` | mcp | optional subtree sandbox; bearer-token lifetime |

> Exact downstream env keys per service (e.g. `FILEENGINE_PG_*`, the bridges'
> `LDAP_*`, `CSAI_PG_*`, `FILEENGINE_CSAI_USER/PASSWORD`, `HTTP_CORS_ORIGIN`,
> `FILEENGINE_EVENTS_ENABLED`, MCP's `FILEENGINE_GRPC_HOST/PORT`, `MCP_HTTP_HOST=0.0.0.0`,
> and the shared `FILEENGINE_LDAP_*` bind/bases, etc.) are derived from these
> high-level inputs in the compose `environment:` blocks. A full per-service env
> map will accompany the compose file.

---

## 10. Persistence (named volumes)

| Volume | Mounted in | Contents |
|--------|-----------|----------|
| `pgdata` | postgres | database cluster |
| `ldapdata` | ldap (389-ds `/data`) | directory database + config |
| `filecache` | core (`FILEENGINE_STORAGE_BASE`) | local storage/cache tier (S3 is the source of truth) |
| `letsencrypt` | nginx | TLS certs/keys (LE or BYO) — **the persistent TLS folder** |
| `ollama` | ollama | pulled AI models (`nomic-embed-text`, …) |
| `redisdata` (optional) | redis | event-stream durability |

**Bind** mounts use `:Z` (SELinux relabel) for Fedora/Podman; **named** volumes do
not (compose warns on `:Z` for named volumes — they are relabeled by the runtime).

---

## 11. AI provider (bundled Ollama, repointable)

- A bundled **`ollama`** service (CPU) provides the embeddings model
  **`nomic-embed-text`** (768-dim). CSAI's embedder — and, by default, its chat
  model — point at this service via `CSAI_*_BASE_URL`.
- **First-startup model install.** A one-shot **`ollama-init`** service pulls the
  embedding model on first boot (mirroring `minio-init`'s bucket-create pattern),
  so initialization — not the first user request — installs the model:

  ```yaml
  ollama:
    image: ollama/ollama
    volumes: [ "ollama-models:/root/.ollama:Z" ]     # pulled models persist
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 10s
      timeout: 5s
      retries: 12

  ollama-init:                                         # one-shot model pull
    image: ollama/ollama
    depends_on:
      ollama: { condition: service_healthy }
    environment:
      OLLAMA_HOST: http://ollama:11434
      CSAI_EMBEDDING_MODEL: ${CSAI_EMBEDDING_MODEL:-nomic-embed-text}
    entrypoint:
      - /bin/sh
      - -c
      - 'ollama pull "$$CSAI_EMBEDDING_MODEL" && echo "model $$CSAI_EMBEDDING_MODEL ready"'
    restart: "no"
  ```

  The pull is **idempotent** (Ollama no-ops if the model is already in the
  persisted `ollama-models` volume), so re-runs are cheap. CSAI (app + worker)
  `depends_on: ollama-init (service_completed_successfully)` so it only starts
  once the model is present — and still tolerates Ollama warm-up (CSAI-2). The
  pulled model name is driven by `CSAI_EMBEDDING_MODEL` so the install and the
  embedder stay in lockstep.
- Providers stay **configurable**: the embedder and the LLM can each be
  **repointed at external services** (OpenAI-compatible/remote Ollama) by
  overriding `CSAI_EMBEDDING_*` / `CSAI_CHAT_*` independently (see CSAI-1 in
  `CODEBASE_ISSUES.md`). CPU chat is workable but slow; a remote LLM is the
  expected upgrade path.
- The embedding **dimension must match the model** (`CSAI_EMBEDDING_DIMENSION=768`
  for `nomic-embed-text`); changing models requires re-provisioning the vector
  tables.
- Ollama models persist to a named volume; CSAI must tolerate Ollama not-yet-ready
  / model-not-pulled at boot (CSAI-2).

---

## 12. Orchestration, startup order & health

- `depends_on` + healthchecks sequence startup:
  `postgres`(healthy) + `ldap`(healthy) → `db-init`(completed) → `core`(healthy)
  → bridges + `csai-app`/`csai-worker` + `mcp` → `nginx`.
- `mcp` depends on `core`(healthy) + `ldap`(healthy) — it binds an LDAP agent
  identity and a gRPC connection at startup and exits if either is unreachable.
- Healthchecks: Postgres `pg_isready`; core via the metrics listener
  (`/healthz` on `8081`); bridges via their health endpoints; CSAI via
  `/health`; mcp via a TCP/HTTP probe on `:8089` (a dedicated `/healthz` is a
  small enhancement — see MCP-1); nginx via a local probe.
- A one-shot **`db-init`** service performs migrations + default-tenant
  provisioning (idempotent; safe to re-run).

---

## 13. Decisions log

| Topic | Decision |
|-------|----------|
| Packaging | **Unified docker-compose** (not a single image); Docker **and Podman** compatible |
| Binaries | **Prebuilt RPMs** (core/http/webdav) + built SPA + CSAI source; AWS SDK RPMs provided |
| Service granularity | **Separate image per service** (core, http-bridge, webdav-bridge, csai, **mcp**, nginx) |
| Routing | **Per-tenant subdomains**: `<tenant>.<base>` (SPA + same-origin `/api`,`/csai`,**`/mcp`**) and `<tenant>-drive.<base>` (WebDAV); SPA + WebDAV derive the active tenant from the host |
| MCP server | **Included** as `fileengine-mcp` (HTTP transport, gRPC+LDAP-backed, already tenant-aware); served at `<tenant>.<base>/mcp`; tool policy via `MCP_*` (delete off by default) |
| Object store | **External S3 only** (no bundled MinIO) |
| Secrets | **All provided in `.env`** (no auto-generation) |
| AI backend | **Bundled Ollama (CPU)** + `nomic-embed-text`; embedder/LLM **repointable** to external providers |
| TLS | **Wildcard `*.<base>`** via `TLS_MODE`: automated DNS-01 (provider API) **/** manual DNS-01 **/** BYO cert — manual and automated are both first-class; persisted to `/etc/letsencrypt` |
| LDAP | **389 Directory Server** (`389ds/dirsrv`, integrated web console); `uid=email`; `ou=people` + `ou=tenants/ou=<tenant>` role `groupOfNames`; membership-based; **templated queries** (LDAP-1) |
| Roles | **Reuse current roles**: users→user, contributors→editor, administrators→admin, system_admin→admin |
| Domains config | **`.env` variables** (no separate YAML) |
| Backups | **Helper scripts provided** (Postgres + LDAP; S3 guidance) |

---

## 14. Podman compatibility

- Standard compose schema only (no Docker-BuildKit-only features); validated with
  `podman compose` / `podman-compose`.
- `:Z` SELinux labels on all volume mounts.
- **Rootless caveat:** binding `80`/`443` rootless requires either running
  rootful, or lowering `net.ipv4.ip_unprivileged_port_start`, or publishing high
  ports behind a host proxy. Documented in the README.
- Healthcheck/`depends_on: condition:` usage kept to the broadly-supported subset.

---

## 15. Repository layout (`docker_unified/`)

```
docker_unified/
├── SPECIFICATION.md
├── IMPLEMENTATION_PLAN.md         (this doc)
├── README.md                      (operator quickstart — to write after assembly)
├── ADMINISTRATOR.md               (day-2 system administration guide — to write after assembly)
├── .env.example                   (to write)
├── docker-compose.yml             (rewritten for the full stack)
├── Makefile                       (build RPMs/SPA, stage artifacts, up/down)
├── images/
│   ├── base/Dockerfile            (Fedora + AWS SDK RPMs)
│   ├── core/Dockerfile
│   ├── http-bridge/Dockerfile
│   ├── webdav-bridge/Dockerfile
│   ├── csai/Dockerfile
│   ├── mcp/Dockerfile             (reuse mcp/Dockerfile; build ctx incl. python_interface)
│   └── nginx/Dockerfile           (SPA + certbot + entrypoint)
├── config/
│   └── nginx/                     (vhost template, ACME, proxy snippets — Phase 6)
├── init/
│   ├── db-init.sh                 ✅ create CSAI DB + vector/pg_trgm + migrations
│   ├── migrations/               (staged CSAI baseline SQL — gitignored)
│   ├── ldap-seed.sh               ✅ 389-ds backend + DIT seed (idempotent)
│   ├── ollama-pull.sh             (pull nomic-embed-text — via ollama-init, Phase 5)
│   └── render-config.sh           (env → nginx/CORS — Phase 6)
├── scripts/
│   └── new-tenant.sh              (provision a tenant OU + role groups in LDAP) ✅ written
├── backup/
│   ├── backup.sh / restore.sh     (Postgres dump + 389-ds db2ldif/ldif2db)
│   └── README.md                  (S3 bucket guidance)
└── rpms/
    ├── aws-sdk/                   (provided, v1.11.725)
    └── fileengine/                (produced by `make`)
```

---

## 16. Implementation phases

1. **Build pipeline** — `Makefile` + base image; produce FileEngine RPMs
   (core with events ON) and the built SPA; stage into `rpms/`/contexts. ✅
   *Done:* `Makefile` builds/stages all three RPM sets + the SPA;
   `images/base/Dockerfile` (Fedora + AWS SDK runtime closure) and
   `images/core/Dockerfile` build; the events RPM installs cleanly (CORE-5
   sysusers fix) and a smoke test brought core+pg+minio up with all localhost
   ports bound and no conflicts, DB schema verified.
2. **Data layer** — Postgres(pgvector) + Redis + `db-init` (migrations, default
   tenant); verify core boots against them with S3. ✅
   *Done:* `docker-compose.yml` (postgres `pgvector/pgvector:pg16`, password
   `redis:7`, one-shot `db-init`, `core`) + `docker-compose.test.yml` (MinIO for
   local S3) + `init/db-init.sh` (creates CSAI DB + `vector`/`pg_trgm`, idempotent)
   + `.env.example`. Verified up: both DBs + extensions provisioned, core boots,
   verifies schema, connects to S3, and logs **"Redis event emission enabled ->
   redis:6379"** — all services healthy.
3. **App layer** — core, http-bridge, webdav-bridge images + compose wiring;
   internal gRPC; verify upload/download/streaming. ✅
   *Done:* `images/http-bridge/` + `images/webdav-bridge/` (RPM-based on the
   shared base; standalone — no fileengine-libs/Postgres; run as `nobody`) wired
   into compose `depends_on: core(healthy)` with internal gRPC + LDAP/CORS env.
   Verified up: both bridges listen (http 8090, webdav 8088) and **http-bridge
   `/readyz` returns ready** — it performs a real gRPC `ListDirectory` against the
   core, confirming the bridge↔core path end-to-end (the same plumbing
   upload/download stream over). The **authenticated** upload/download/streaming
   test needs an LDAP-issued token, so it rides with Phase 4 (LDAP).
4. **LDAP** — 389-ds (`389ds/dirsrv`) + seed LDIFs/`dsconf` (uid=email,
   tenants/role groups) + web console; align the bridges' LDAP config to
   `ou=tenants` (LDAP-1); verify login → tenant + roles. ✅
   *Done:* `ldap` (389-ds) + one-shot `ldap-init` (`init/ldap-seed.sh`: creates
   the suffix backend via `dsconf --create-suffix`, seeds `ou=users`/`ou=tenants`/
   default-tenant role groups + the initial admin). Bridges wired to it
   (`depends_on: ldap-init`). **Verified end to end:** LDAP login →
   `POST /v1/auth/token`; `whoami` returned `tenant=default`,
   `roles=[users,contributors,administrators,system_admin]`; and the **deferred
   Phase-3 transfer test passed** — create-in-root (system_admin) → 1 MiB
   streaming upload (HTTP 204) → download with exact sha256 roundtrip, which also
   emitted `file.created`/`file.updated` to the compose Redis (full events
   pipeline). *(Default-tenant role resolution confirmed; the LDAP-1 cleanup is
   still recommended for clean non-default-tenant role resolution.)*
5. **CSAI + Ollama** — csai image (+ conversion tools), app + worker; bundled
   `ollama` service + `nomic-embed-text` model pull; verify event-driven previews,
   on-demand convert, vector search/chat; confirm embedder/LLM are independently
   repointable (CSAI-1) and CSAI tolerates Ollama warm-up (CSAI-2).
6. **MCP** — `fileengine-mcp` image (HTTP transport, build ctx incl.
   `python_interface`); wire to core gRPC + LDAP; route `<tenant>.<base>/mcp` via
   nginx; verify per-request auth (Basic/Bearer) and tenant-from-subdomain, and
   the tool-exposure policy (`MCP_READ_ONLY`/`MCP_ALLOW_DELETE`).
7. **nginx + TLS** — SPA serving, **per-tenant subdomain routing** (incl. the
   `-drive` WebDAV vhost and the `/mcp` path), and the wildcard cert via
   **`TLS_MODE`** (automated DNS-01 / manual DNS-01 / BYO) with persistent certs +
   renewal; same-origin SPA + **subdomain tenant selection** verified end-to-end
   (FE-1, FE-2).
8. **Backups** — helper scripts: Postgres dump/restore, LDAP export/import
   (389-ds `dsctl … db2ldif` / `ldif2db`, or `dsconf backup`), and documented
   S3 bucket guidance.
9. **Hardening & docs** — healthchecks/ordering, `.env.example`, Podman
   validation, and (once the stack is assembled) the two operator docs:
   - **README** — quickstart: deploy steps, point DNS→IP, first-run, cert
     renewal, the new-tenant procedure.
   - **ADMINISTRATOR.md** — day-2 administration: tenant lifecycle (`new-tenant.sh`
     + LDAP console), user/role management, TLS/cert rotation, backup & restore
     (Postgres + 389-ds + S3), Ollama model management, the MCP tool-exposure
     policy (`MCP_*`), log/audit locations, healthchecks & troubleshooting,
     upgrades (rebuild RPMs/images), and the `AT_REST_KEY` safekeeping warning.

---

## 17. Resolved review items

All review questions are resolved (see the decisions log, §13):

1. **LDAP** — **389 Directory Server** (`389ds/dirsrv`) with integrated web console. ✓
2. **Domains config** — **`.env` variables** (no YAML). ✓
3. **Bridge ↔ LDAP** — make LDAP queries **templated/configurable** rather than
   hard-coded; tracked as **LDAP-1** in `CODEBASE_ISSUES.md` (with **LDAP-2** for
   WebDAV tenant resolution). ✓
4. **AI** — **bundle Ollama** (CPU) + `nomic-embed-text` (768-dim), repointable. ✓
5. **Backups** — **include helper scripts** (Postgres + LDAP; S3 guidance). ✓
6. **Routing** — **per-tenant subdomains**: `<tenant>.<base>` (SPA + same-origin
   `/api`,`/csai`) and `<tenant>-drive.<base>` (WebDAV). SPA selects the active
   tenant from its subdomain (**FE-2**); WebDAV takes the first `-`-segment of the
   host label (**LDAP-2**). ✓
7. **TLS / domain** — **wildcard `*.<base>`** via `TLS_MODE`, with both **manual
   and API-automated** options: automated DNS-01 (provider API) / manual DNS-01 /
   BYO cert; persisted to `/etc/letsencrypt`. DNS records via a wildcard A record
   (manual or API). ✓
8. **MCP server** — **included** as `fileengine-mcp` (HTTP transport, gRPC+LDAP,
   already tenant-aware); served same-origin at `<tenant>.<base>/mcp`; tool policy
   via `MCP_*` (delete off by default). See §3, §5, MCP-1/2 in `CODEBASE_ISSUES.md`. ✓

The remaining work is captured as build phases (§16) and the source-code changes
in `CODEBASE_ISSUES.md`.
