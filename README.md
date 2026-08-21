# Unified FileEngine Stack

> ⚠️ **Active development — not production-ready.** This project is under active development and should **not** be considered safe for mission-critical use.

A single `docker compose` deployment of the whole FileEngine platform — the gRPC
core, the REST (`http-bridge`) and WebDAV (`webdav-bridge`) gateways, the
Convert/Search/AI service (`csai` app + worker) with a bundled Ollama, the
discussion service (document-anchored comments & review), `folder-actions`
(event-driven actions bound to a folder), `difference` (version-to-version
comparison renditions), `ldap-manager` (tenant user/role admin),
the MCP server for AI agents, an ONLYOFFICE Document Server for in-browser office
editing, 389 Directory Server, Postgres (pgvector), Redis, and an nginx that
terminates TLS and routes per-tenant subdomains.

- **Specification:** [`SPECIFICATION.md`](SPECIFICATION.md)
- **Design & build phases:** [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
- **Day-2 administration:** [`ADMINISTRATOR.md`](ADMINISTRATOR.md)
- **Source-code follow-ups:** [`CODEBASE_ISSUES.md`](CODEBASE_ISSUES.md)

## What you need

- Docker (or Podman) with the `compose` plugin.
- An external **S3-compatible bucket** + access keys. *(For local testing the
  `docker-compose.test.yml` override runs a throwaway MinIO instead.)*
- Postgres is **bundled** by default. To run against a managed server instead, set
  `POSTGRES_HOST`/`POSTGRES_PORT` in `.env` and add
  `-f docker-compose.external-data.yml` — see `DEPLOYMENT_CLOUD.md` §7.1.
- A **domain** with a **wildcard DNS A record** `*.<base> → host IP` (so every
  tenant subdomain resolves). *(For local testing, use `/etc/hosts` aliases.)*

## Topology

Only **nginx** publishes host ports (`80`/`443`); everything else talks over the
internal compose network. Each tenant `<t>` of `BASE_DOMAIN` gets:

| Host | Serves |
|------|--------|
| `<t>.<base>` | SPA + same-origin `/api` (http-bridge), `/csai` (csai-app), `/mcp` (mcp), `/discuss` (discussion), `/folder-actions` (folder-actions), `/diff` (difference), `/ldapadmin` (ldap-manager) |
| `<t>-drive.<base>` | WebDAV (webdav-bridge) |

Tenant names contain **no hyphen**, so the SPA host and the `-drive` host never
collide.

Two **non-tenant** hosts are also served, both covered by the same `*.<base>`
wildcard DNS/cert:

| Host | Serves |
|------|--------|
| `login.<base>` | The shared sign-in origin (see below) |
| `docs.<base>` | ONLYOFFICE Document Server (in-browser office editing) |

For the editor, set `ONLYOFFICE_JWT_SECRET` in `.env`; editing requires
`TLS_MODE` other than `none` (the browser loads `https://docs.<base>`).

### The shared sign-in origin

`login.<base>` is a **reserved label**: it never resolves to a tenant. A
signed-out visitor to any tenant host is sent here, signs in once, and is handed
back to a workspace they belong to.

The point is OAuth. Every provider is registered with **one** redirect URI and
`OAUTH_RETURN_ALLOWLIST` holds **one** entry, however many tenants exist —
instead of both growing per tenant and needing a bridge restart each time.

```sh
LOGIN_SUBDOMAIN=login       # .env — the default
```

Configurable because a deployment may be unable to reserve `login` on its
domain. Whatever you choose:

- **It must not contain a hyphen.** The leading DNS label is split on `-` to
  separate `<tenant>-<interface>` — the rule behind `<t>-drive` — so
  `acme-login` would resolve to the *tenant* `acme` and the sign-in origin would
  double as a tenant host. The bridge refuses to start on a label it could never
  match, rather than reserving nothing and saying nothing about it.
- `OAUTH_REDIRECT_BASE` and `OAUTH_RETURN_ALLOWLIST` derive from it, so they
  follow automatically unless you set them explicitly.
- The SPA learns the label from the bridge at run time, so one prebuilt image
  serves deployments that use different labels.

After signing in, the SPA sends the user on to their workspace's own origin —
but only after checking that origin is serving the app. A tenant whose subdomain
is not reachable is served from the sign-in origin instead, with the tenant
carried in `X-Tenant`. Nothing is broken when that happens; it just does not
leave `login.<base>`. With wildcard DNS in place, every tenant is reachable and
the forward is the normal path.

## Deploy

```sh
# 1. Configure (no secrets are auto-generated — set them all).
cp .env.example .env && $EDITOR .env        # BASE_DOMAIN, S3_*, passwords, AT_REST_KEY, …

# 2. Build artifacts (RPMs with Redis events ON, the SPA, and stage the Python
#    service sources). No domain needed: the SPA reads tenancy from the request
#    host and learns the sign-in label from the bridge, both at RUN time, so one
#    build serves any domain.
make build

# 3. Build the images (the shared base first, then the services).
make base-image
docker compose build

# 4. Point *.host.com -> your host IP (wildcard A record), then bring it up.
docker compose up -d
```

First start runs the one-shots automatically: `db-init` (CSAI DB + extensions),
`ldap-init` (directory + initial admin), `ollama-init` (pull `nomic-embed-text`),
and (test override) `minio-init`. Then browse `https://<tenant>.<base>` and log
in as the seeded admin (`LDAP_ADMIN_EMAIL` / `LDAP_ADMIN_PASSWORD`).

## Minimal production deployment (external S3)

The base `docker-compose.yml` expects a **real external S3 bucket** — the
throwaway MinIO exists **only** in the `docker-compose.test.yml` override. For a
minimal production install, bring the stack up from the base compose file alone
(no `-f docker-compose.test.yml`) and point it at your bucket.

1. **Provision the bucket.** Create an S3 bucket up front (the stack does not
   create it) and a dedicated access key/secret with read+write on it. Works with
   AWS S3 or any S3-compatible store (MinIO, Ceph RGW, Backblaze B2, Wasabi).

2. **Configure `.env`** (`cp .env.example .env`). The production-critical keys:

   ```ini
   BASE_DOMAIN=host.com               # public apex; point *.host.com at this host
   TLS_MODE=letsencrypt-dns           # real wildcard cert (see TLS section)
   LE_DNS_PROVIDER=cloudflare         # your DNS-01 provider

   # External object store — the file content lives here.
   S3_ENDPOINT=https://s3.us-east-1.amazonaws.com
   S3_REGION=us-east-1
   S3_BUCKET=your-fileengine-bucket
   S3_ACCESS_KEY=...                  # key scoped to that bucket
   S3_SECRET_KEY=...
   S3_PATH_STYLE=false                # false for AWS S3; true for MinIO/Ceph/B2

   # At-rest crypto — keep ENCRYPT on; keep AT_REST_KEY STABLE and BACKED UP
   # (losing it makes stored objects unreadable).
   FILEENGINE_ENCRYPT_DATA=true
   AT_REST_KEY=$(openssl rand -hex 32)

   # Set every secret to a strong unique value.
   POSTGRES_PASSWORD=...   REDIS_PASSWORD=...   LDAP_BIND_PASSWORD=...
   LDAP_ADMIN_PASSWORD=...
   FILEENGINE_JWT_SECRET=$(openssl rand -hex 32)
   ONLYOFFICE_JWT_SECRET=$(openssl rand -hex 32)   # shared Doc Server <-> CSAI editor JWT
   ```

3. **Build and bring up** — base compose only, so no MinIO is started:

   ```sh
   make build BASE_DOMAIN=host.com
   make base-image && docker compose build
   docker compose up -d                # note: NO -f docker-compose.test.yml
   ```

4. **Point `*.host.com`** at the host, then confirm the wildcard cert issued
   (`docker compose logs nginx`) and browse `https://<tenant>.host.com`.

Back up the bucket with the provider's versioning/replication, and keep
`AT_REST_KEY` plus the Postgres/LDAP dumps (`backup/backup.sh`) safe — see
[Backups](#backups).

## TLS (`TLS_MODE`)

| Mode | Behaviour |
|------|-----------|
| `letsencrypt-dns` | Wildcard via certbot **DNS-01** using the provider API (`LE_DNS_PROVIDER`); auto-renew. |
| `letsencrypt-manual` | certbot prints a TXT record you add by hand. |
| `byo` | You mount `fullchain.pem`+`privkey.pem` into the `nginxtls` volume. |
| `selfsigned` | A self-signed wildcard (dev). |
| `none` | **Unsecured plain HTTP — testing only.** |

Wildcards require DNS-01 (HTTP-01 can't issue them). To issue/renew manually:
```sh
docker compose exec nginx obtain-cert.sh && docker compose exec nginx nginx -s reload
```

## Local testing (unsecured, no DNS)

```sh
# .env: BASE_DOMAIN=example.com  TLS_MODE=none
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d   # adds MinIO
```
Add to `/etc/hosts`:
```
127.0.0.1   example.com   default.example.com   default-drive.example.com
```
Then open `http://default.example.com` (the seeded tenant is `default`).

## Tenants & users

- **New tenant:** `scripts/new-tenant.sh <name> --admin <email>` (creates the
  LDAP OU + role groups; the core/CSAI provision DB schemas + storage on first
  access). See [`LDAP_REFERENCE.md`](LDAP_REFERENCE.md).
- **Users/roles:** managed in LDAP (the 389-ds Cockpit console, or `ldapmodify`).
  See [`ADMINISTRATOR.md`](ADMINISTRATOR.md).

## Backups

`backup/backup.sh` dumps Postgres + LDAP + `.env`; the S3 bucket holds the file
content (back it up with the provider's versioning/replication). See
[`backup/README.md`](backup/README.md). **Keep `AT_REST_KEY` safe** — losing it
makes the at-rest object data unreadable.

## Operate

```sh
docker compose ps                     # status / health
docker compose logs -f <service>      # logs (all services log to stdout)
docker compose down                   # stop (keep data);  down -v  also drops volumes
```
See [`ADMINISTRATOR.md`](ADMINISTRATOR.md) for day-2 administration.

## License

Copyright (C) 2026 James Hickman <james@rationalboxes.com>

This project is licensed under the **GNU Affero General Public License, version 3 (or
later)** — see the [LICENSE](LICENSE) file for the full text.
