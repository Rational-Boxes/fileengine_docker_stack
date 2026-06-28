# Unified FileEngine Stack

A single `docker compose` deployment of the whole FileEngine platform — the gRPC
core, the REST (`http-bridge`) and WebDAV (`webdav-bridge`) gateways, the
Convert/Search/AI service (`csai` app + worker) with a bundled Ollama, the MCP
server for AI agents, 389 Directory Server, Postgres (pgvector), Redis, and an
nginx that terminates TLS and routes per-tenant subdomains.

- **Specification:** [`SPECIFICATION.md`](SPECIFICATION.md)
- **Design & build phases:** [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
- **Day-2 administration:** [`ADMINISTRATOR.md`](ADMINISTRATOR.md)
- **Source-code follow-ups:** [`CODEBASE_ISSUES.md`](CODEBASE_ISSUES.md)

## What you need

- Docker (or Podman) with the `compose` plugin.
- An external **S3-compatible bucket** + access keys. *(For local testing the
  `docker-compose.test.yml` override runs a throwaway MinIO instead.)*
- A **domain** with a **wildcard DNS A record** `*.<base> → host IP` (so every
  tenant subdomain resolves). *(For local testing, use `/etc/hosts` aliases.)*

## Topology

Only **nginx** publishes host ports (`80`/`443`); everything else talks over the
internal compose network. Each tenant `<t>` of `BASE_DOMAIN` gets:

| Host | Serves |
|------|--------|
| `<t>.<base>` | SPA + same-origin `/api` (http-bridge), `/csai` (csai-app), `/mcp` (mcp) |
| `<t>-drive.<base>` | WebDAV (webdav-bridge) |

Tenant names contain **no hyphen**, so the SPA host and the `-drive` host never
collide.

## Deploy

```sh
# 1. Configure (no secrets are auto-generated — set them all).
cp .env.example .env && $EDITOR .env        # BASE_DOMAIN, S3_*, passwords, AT_REST_KEY, …

# 2. Build artifacts (RPMs with Redis events ON, the subdomain-aware SPA, and
#    stage the Python service sources). Pass your domain so the SPA is built
#    subdomain-aware.
make build BASE_DOMAIN=host.com

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

This project is licensed under the **GNU General Public License, version 3 (or
later)** — see the [LICENSE](LICENSE) file for the full text.
