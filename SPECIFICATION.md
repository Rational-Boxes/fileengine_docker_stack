# Unified FileEngine Stack — Specification

A unified **docker-compose** deployment of the entire FileEngine application
stack, runnable on conventional Docker- or Podman-based cloud hosting. It needs
only to be **pointed at an external S3-compatible object store** and have its
**domain's DNS pointed at the host IP**; TLS is obtained automatically via
Let's Encrypt.

> See `IMPLEMENTATION_PLAN.md` for the detailed design and build plan, and
> `CODEBASE_ISSUES.md` for the source-code changes required in the main repos.

## Goals

- One `docker-compose.yml` brings up the whole stack.
- Deploy by editing a single `.env`, pointing at an S3 bucket, and pointing DNS
  at the IP. Automatic Let's Encrypt TLS.
- Docker **and** Podman compatible.

## Non-goals (this phase)

- A single all-in-one image (this is a multi-service compose stack).
- Bundling the object store (S3 is **external**).
- Kubernetes/HA/replication.

## Services (one image each)

- **core** — FileEngine gRPC service (internal only; trusted-access).
- **http-bridge** — REST/auth bridge for the SPA.
- **webdav-bridge** — WebDAV interface.
- **csai-app** — Convert/Search/AI service (FastAPI).
- **csai-worker** — CSAI event-driven ingest/rendition worker (same image as
  `csai-app`, different command).
- **nginx** — terminates TLS, serves the built SPA, and reverse-proxies the APIs.

All FileEngine images are **Fedora-based** and install the **prebuilt FileEngine
RPMs** (core/http/webdav) plus the **provided AWS SDK RPMs** (`rpms/aws-sdk/`).
The core RPM is built with Redis **event emission enabled** (required for
automatic preview generation). The SPA is built for **same-origin** paths and
served by nginx.

## Bundled infrastructure (compose services)

- **postgres** — Postgres 16 with **pgvector** (CSAI requires the `vector`
  extension). Hosts two databases: `fileengine` (per-tenant schemas) and
  `convert_search_ai`.
- **redis** — password-protected; carries the core's event stream and CSAI's
  cache invalidation.
- **ldap** — **389 Directory Server** (`389ds/dirsrv`) with its integrated
  Cockpit-based **web console** for directory administration; seeded with the
  base directory and the initial administrative user. Directory model in
  [LDAP](#ldap-directory-model).
- **ollama** — bundled local AI (CPU) serving the **embedding model**
  (`nomic-embed-text`); CSAI points its embedder (and, by default, its chat
  model) here. Providers remain **configurable** so the embedder and LLM can be
  repointed at external services later.

## Routing (per-tenant subdomains)

Each tenant `<tenant>` of the base domain `<base>` gets two hostnames:

| Host | Serves |
|------|--------|
| `<tenant>.<base>` | SPA + same-origin `/api/` (http-bridge) + `/csai/` (csai-app) |
| `<tenant>-drive.<base>` | WebDAV (webdav-bridge) |

- The **SPA derives the active tenant from its subdomain** (`<tenant>.<base>`).
  A user with access to multiple tenants switches by visiting that tenant's
  subdomain; the in-app tenant selector remains for convenience.
- **WebDAV resolves the tenant from the host** by splitting the first label on
  `-` and taking the first segment (`someco-drive.<base>` → `someco`). Tenant
  names therefore must not contain a hyphen.
- nginx exposes only `80`/`443`; the core gRPC port is never published. Within a
  subdomain the SPA and its `/api`+`/csai` are same-origin, so CORS stays minimal.

## Domain & TLS — manual and API-automated options

Per-tenant subdomains need a **wildcard certificate** (HTTP-01 can't issue
wildcards). Both manual and API-automated paths are first-class, via `TLS_MODE`:

- **`letsencrypt-dns` (default, automated):** certbot's DNS plugin uses the DNS
  provider **API** to answer the DNS-01 challenge and **auto-renew** the
  `*.<base>` wildcard, unattended. Needs DNS provider API credentials.
- **`letsencrypt-manual`:** certbot prints the `_acme-challenge` TXT record; the
  operator adds it at any provider by hand (no API creds; renewals re-run).
- **`byo`:** the operator supplies a wildcard cert/key in the mounted TLS folder
  (commercial / internal CA / DNS-01 elsewhere); no ACME in the stack.

Certs persist to a **mounted `/etc/letsencrypt`** (or BYO) volume; a self-signed
bootstrap lets nginx start first. **DNS records** are equally flexible: a single
**wildcard A record `*.<base> → IP`** (set manually or via API) covers all tenant
subdomains, so adding a tenant needs no cert or DNS work.

## Object store (external)

An S3-compatible endpoint/bucket/keys are provided via `.env`. The object store
is the source of truth; each service keeps only a local cache tier.

## LDAP directory model

`uid = email`. Tenants are modeled as OUs of role groups; a user's tenants and
roles are derived from `groupOfNames` **membership**.

```
dc=<domain>
├── ou=people                       # person entries; uid = full email
│   └── uid=admin@<domain>          # seeded initial admin (creds from .env)
└── ou=tenants
    └── ou=default                  # one OU per tenant
        ├── cn=system_admin   (groupOfNames)   # → admin
        ├── cn=administrators (groupOfNames)   # → admin
        ├── cn=contributors   (groupOfNames)   # → editor
        └── cn=users          (groupOfNames)   # → user
```

A new tenant is a new `ou=<tenant>,ou=tenants` containing the role groups —
create it with `scripts/new-tenant.sh`. The bridges' LDAP lookups are already
**site-configurable** via the `FILEENGINE_LDAP_*` bases (see `LDAP_REFERENCE.md`).
The **active tenant** is determined by host: the SPA/HTTP path from the
`<tenant>.<base>` subdomain, and **WebDAV from the `<tenant>-drive.<base>` host**.

## Configuration

- A single operator-provided **`.env`** holds all secrets and tunables (S3,
  `AT_REST_KEY`, DB/Redis/LDAP passwords, the initial admin, the primary domain,
  Let's Encrypt email, AI settings). **No secrets are auto-generated.**
- A small init step renders the nginx wildcard vhosts and CORS allow-lists from
  the high-level inputs (`BASE_DOMAIN`, `EXTRA_CORS_ORIGINS`, DNS-01 creds).

## Persistence

Named volumes for: Postgres data, LDAP (389-ds) data, the FileEngine local
cache (`FILEENGINE_STORAGE_BASE`), the Ollama models, and the **TLS folder**
(`/etc/letsencrypt`). All mounts use `:Z` for SELinux (Fedora/Podman).

## Operations

- One-shot `db-init` runs migrations and provisions the **default tenant**
  (idempotent).
- **Backup helper scripts** are provided for Postgres, the LDAP directory, and
  guidance for the external S3 bucket.
- Healthchecks + `depends_on` sequence startup
  (postgres/ldap → db-init → core → bridges/csai → nginx).
