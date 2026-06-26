# FileEngine — Administrator Guide

Day-2 operation of the unified stack. For first-time deploy see
[`README.md`](README.md); for the design see
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).

All commands run from the `docker_unified/` directory. Add the test override
(`-f docker-compose.yml -f docker-compose.test.yml`) only when running with the
bundled MinIO; in production omit it (external S3).

---

## 1. Services & ports

| Service | Role | Internal port | Published |
|---------|------|---------------|-----------|
| `nginx` | TLS + SPA + per-tenant routing | 80/443 | **yes (only this)** |
| `core` | gRPC filesystem (trusted-access) | 50051, metrics 8081 | no |
| `http-bridge` | REST/auth proxy → `/api` | 8090 (mon 8091) | no |
| `webdav-bridge` | WebDAV → `-drive` host | 8088 (mon 8089) | no |
| `csai-app` | search / RAG chat / preview → `/csai` | 8092 | no |
| `csai-worker` | event-driven ingest/rendition | — | no |
| `mcp` | MCP server → `/mcp` | 8089 | no |
| `ldap` | 389 Directory Server | 3389/3636 | no |
| `postgres` | pgvector (core + CSAI DBs) | 5432 | no |
| `redis` | event stream + cache invalidation | 6379 | no |
| `ollama` | CPU embedding model | 11434 | no |

One-shots that run on `up` and exit: `db-init`, `ldap-init`, `ollama-init`
(+ `minio-init` with the test override). They are **idempotent** — re-running
`up` re-runs them harmlessly.

```sh
docker compose ps                       # status + health
docker compose logs -f csai-worker      # follow one service
docker compose restart http-bridge      # restart a service
```

---

## 2. Configuration

All config is in `.env` (no secrets are auto-generated). Key groups — see
`.env.example` for the full list and inline docs:

- **Domain/TLS:** `BASE_DOMAIN`, `TLS_MODE`, `LE_*`, `HTTP_MAX_BODY_BYTES`.
- **Postgres:** `POSTGRES_USER/PASSWORD`, `CORE_DB`, `CSAI_DB`.
- **Redis:** `REDIS_PASSWORD`.
- **S3:** `S3_ENDPOINT/REGION/BUCKET/ACCESS_KEY/SECRET_KEY/PATH_STYLE`.
- **At-rest:** `FILEENGINE_ENCRYPT_DATA/COMPRESS_DATA`, **`AT_REST_KEY`**.
- **LDAP:** `LDAP_DOMAIN` (suffix), `LDAP_BIND_DN/PASSWORD`, `LDAP_*_BASE`,
  `LDAP_ADMIN_EMAIL/PASSWORD`, `DEFAULT_TENANT`.
- **AI:** `CSAI_EMBEDDING_*` (independent of) `CSAI_CHAT_*`; `INSTALL_DOCLING`.
- **MCP policy:** `MCP_READ_ONLY`, `MCP_ALLOW_DELETE`.

After editing `.env`, apply with `docker compose up -d` (recreates the services
whose env changed). The SPA's `BASE_DOMAIN` is **baked at build time** — change
it with `make spa BASE_DOMAIN=… && docker compose build nginx && docker compose up -d nginx`.

---

## 3. Tenants

A tenant is an OU of role groups under `ou=tenants`; its DB schema and storage
are created **on first access** (no provisioning command).

**Create a tenant** (LDAP side) with the helper:
```sh
# uses the FILEENGINE_LDAP_* env / flags; --container runs ldapadd in the ldap container
scripts/new-tenant.sh acme --admin alice@example.com --container ldap
```
This adds `ou=acme,ou=tenants,<suffix>` with `users`/`contributors`/
`administrators`/`system_admin` `groupOfNames`. Point DNS (the wildcard already
covers `acme.<base>` and `acme-drive.<base>`), then a user who logs in at
`acme.<base>` gets the tenant provisioned on first write.

> **Tenant names contain no hyphen** (the WebDAV host splits the label on `-`,
> and it must be a safe Postgres schema identifier).

---

## 4. Users & roles (LDAP)

Identity lives in 389-ds. Use the LDAP CLI tools inside the container (the
`dsidm`/`dsconf` admin tools and `ldapmodify` are all present):

**Add a user** (`uid` = email):
```sh
docker compose exec -T ldap ldapadd -x -H ldap://localhost:3389 \
  -D "cn=Directory Manager" -w "$LDAP_BIND_PASSWORD" <<'LDIF'
dn: uid=bob@example.com,ou=users,dc=example,dc=com
objectClass: inetOrgPerson
uid: bob@example.com
cn: Bob Smith
sn: Smith
mail: bob@example.com
userPassword: <password>
LDIF
```

**Grant a role** in a tenant (add the user DN to the role group):
```sh
docker compose exec -T ldap ldapmodify -x -H ldap://localhost:3389 \
  -D "cn=Directory Manager" -w "$LDAP_BIND_PASSWORD" <<'LDIF'
dn: cn=contributors,ou=default,ou=tenants,dc=example,dc=com
changetype: modify
add: member
member: uid=bob@example.com,ou=users,dc=example,dc=com
LDIF
```

**Role → access level** (resolved from group membership, returned by `whoami`):

| Role group (`cn`) | Access |
|-------------------|--------|
| `system_admin`, `administrators` | admin |
| `contributors` | editor |
| `users` | user |

A user's tenants and roles come from which `groupOfNames` they're a `member` of.
389-ds also ships a Cockpit web console (not published by default in this
compose; expose `:9090` or use the CLI tools above).

---

## 5. TLS / certificate rotation

`TLS_MODE` (see README) selects how nginx gets its wildcard cert. For Let's
Encrypt, issuance/renewal is a certbot DNS-01 run (HTTP-01 can't do wildcards):

```sh
docker compose exec nginx obtain-cert.sh           # issue/renew (uses LE_* env)
docker compose exec nginx nginx -s reload          # pick up the new cert
```
Automate renewal from host cron (twice-daily is the LE recommendation):
```
0 3,15 * * *  cd /opt/fileengine && docker compose exec -T nginx obtain-cert.sh \
              && docker compose exec -T nginx nginx -s reload
```
Certs persist in the `letsencrypt` volume. For **BYO**, drop `fullchain.pem` +
`privkey.pem` into the `nginxtls` volume and set `TLS_MODE=byo`.

---

## 6. AI: Ollama models & providers

The embedder and the chat LLM are configured **independently** — the boot banner
(`docker compose logs csai-app | grep "AI providers"`) shows both.

- **Embedder** (`CSAI_EMBEDDING_*`): defaults to the bundled Ollama
  `nomic-embed-text` (768-dim). The dimension **must match the model**; changing
  the model means re-provisioning the per-tenant vector tables and re-embedding.
- **Chat** (`CSAI_CHAT_*`): point at an external provider (Anthropic/OpenAI/an
  OpenAI-compatible endpoint) — CPU chat on Ollama works but is slow.

Manage Ollama models:
```sh
docker compose exec ollama ollama list
docker compose exec ollama ollama pull <model>     # persisted in the 'ollama' volume
```
Repoint the embedder/LLM by editing `CSAI_EMBEDDING_*` / `CSAI_CHAT_*` in `.env`
and `docker compose up -d csai-app csai-worker`.

---

## 7. MCP tool-exposure policy

The MCP server (`/mcp`) authenticates each request against LDAP and resolves the
tenant from the Host subdomain. Constrain what agents can do via `.env`:

- `MCP_ALLOW_DELETE=0` (default) — soft-delete/undelete tools hidden.
- `MCP_READ_ONLY=1` — hide all write tools.
- `MCP_MAX_READ_BYTES` / `MCP_MAX_WRITE_BYTES` / `MCP_MAX_RESULTS` — per-call caps.
- `MCP_SUBTREE_ALLOWLIST` — sandbox an agent to specific subtree UIDs.

Apply with `docker compose up -d mcp`.

---

## 8. Backup & restore

```sh
backup/backup.sh                  # -> backups/<ts>/ : Postgres + LDAP + .env
backup/restore.sh backups/<ts>    # restore into a running stack
```
The **S3 bucket** (file content) is backed up with the provider's own
versioning/replication. Full details + the **`AT_REST_KEY` warning** in
[`backup/README.md`](backup/README.md).

---

## 9. Logs & audit

All services log to stdout → `docker compose logs <service>`.

- **Core** activity events go to Redis (`fileengine:events`); the CSAI worker
  consumes them for previews/indexing.
- **CSAI audit:** set `CSAI_AUDIT_LOG_FILE` (else stderr) — records search /
  document-text / chat access.
- **MCP audit:** set `MCP_AUDIT_LOG_FILE` (else stderr) — every tool call
  (`ts, user, tenant, tool, uid, result`).

---

## 10. Health & troubleshooting

`docker compose ps` shows per-service health. Quick internal probes:
```sh
docker compose exec http-bridge sh -c 'curl -s localhost:8090/readyz'   # bridge↔core gRPC
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" ping
docker compose exec postgres pg_isready -U "$POSTGRES_USER"
```

Common situations:

- **No previews / nothing searchable.** The core RPM must be built with Redis
  events ON (it is, in this stack); confirm `core` logs `Redis event emission
  enabled` and the `csai-worker` logs `convert … -> indexed`. A tenant's CSAI
  tables are created on its **first ingest** — searching a tenant before any
  upload returns empty/errors until then.
- **Embeddings fail at first.** `csai-*` wait for `ollama-init` to pull the
  model; on a cold start give it a minute (the model persists afterwards).
- **`[StorageTracker] … /storage/<tenant> … No such file`** on first boot is
  **benign** (the tenant storage dir doesn't exist until the first write).
- **Port already in use** on `up`: only nginx publishes `80`/`443`; free them or
  change the published ports.
- **LDAP login fails.** Check `ldap-init` completed and the user is a `member`
  of a role group in the tenant (`whoami` returns the roles).

---

## 11. Upgrades

To roll out new code (core/bridges/CSAI/MCP/SPA):
```sh
make build BASE_DOMAIN=host.com     # rebuild RPMs + SPA, re-stage sources
make base-image                     # if core/bridge runtime libs changed
docker compose build                # rebuild service images
docker compose up -d                # recreate changed services
```
The RPMs are built from each source repo's **committed HEAD**. Data volumes
(`pgdata`, `ldapdata`, `filecache`, `ollama`, TLS) survive image rebuilds.

---

## 12. Secrets

- **`AT_REST_KEY`** is the most critical secret: objects are encrypted at rest
  with it. **Losing it makes the S3 data unreadable.** Store it in a secrets
  manager; it must stay **stable** across the deployment's life.
- The LDAP bind currently uses the Directory Manager (root) account — see
  **LDAP-3** in `CODEBASE_ISSUES.md` to move to a least-privilege service account.
- `.env` holds all secrets — keep it `0600` and out of version control (it is
  gitignored).
