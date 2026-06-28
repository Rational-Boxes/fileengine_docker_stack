# Cloud Deployment Guide — AWS & Other Providers

Step-by-step deployment of the **Unified FileEngine Stack** to a cloud host. This
complements [`README.md`](README.md) (quick start) and [`ADMINISTRATOR.md`](ADMINISTRATOR.md)
(day-2 ops); read those for component detail. Everything here is about getting a
production-grade deployment running on a cloud provider.

---

## 1. What gets deployed

The entire platform runs as **one `docker compose` project** on a single Linux
host (VM/instance). Only **nginx** publishes host ports (`80`/`443`); every other
service talks over the internal compose network and is never exposed publicly.

| Service | Role | Exposed? |
|---|---|---|
| `nginx` | TLS termination + per-tenant subdomain routing + serves the SPA | **80/443 only** |
| `core` | gRPC file engine (events ON) | internal `:50051` |
| `http-bridge` | REST gateway (`/api`) + auth/token + LDAP introspection | internal `:8090/8091` |
| `webdav-bridge` | WebDAV gateway (`<t>-drive.<base>`) | internal |
| `csai-app` / `csai-worker` | Convert/Search/AI (RAG chat, search, indexing) | internal `:8092` |
| `mcp` | MCP server for AI agents (`/mcp`) | internal |
| `postgres` (pgvector) | core metadata + CSAI vectors | internal `:5432` |
| `redis` | core event stream + CSAI cache invalidation | internal `:6379` |
| `ldap` (389-ds) | auth + tenant/role groups | internal `:3389` |
| `ollama` | bundled CPU embeddings (`nomic-embed-text`) | internal `:11434` |

**External dependency you must provide:** an **S3-compatible object store** for
file content (AWS S3, GCS in S3-interop mode, Azure via MinIO gateway, DO Spaces,
MinIO, …). Everything else is in the compose project.

### Tenancy & DNS
Each tenant `<t>` of `BASE_DOMAIN` is a subdomain:
- `<t>.<base>` → SPA + same-origin `/api`, `/csai`, `/mcp`
- `<t>-drive.<base>` → WebDAV

This **requires a wildcard DNS record** `*.<base> → host IP`. Tenant names contain
no hyphen so the SPA host and the `-drive` host never collide.

---

## 2. Choose a deployment model

| Model | When | Postgres / Redis / LDAP / S3 |
|---|---|---|
| **A. All-in-one** (default compose) | Pilots, small/medium installs, single host | All bundled in compose; S3 external |
| **B. Externalized data layer** (recommended for production) | HA, backups, scaling | Postgres→RDS/Cloud SQL, Redis→ElastiCache/Memorystore, S3→managed, LDAP→bundled or managed |

This guide does **Model A** end-to-end (it's the supported path), then §7 lists the
exact changes to move each datastore to a managed service (Model B).

---

## 3. Sizing & cost baseline

The bundled **Ollama** (CPU embeddings) and **docling** (ML PDF extraction in CSAI)
are the memory-hungry parts.

| Profile | vCPU | RAM | Disk | Notes |
|---|---|---|---|---|
| Minimum (pilot) | 4 | 16 GB | 60 GB | `INSTALL_DOCLING=0` to lighten CSAI |
| Recommended | 8 | 32 GB | 100 GB+ | docling on; comfortable headroom |
| Heavy ingest | 16 | 64 GB | 200 GB+ | large corpora / many tenants |

Disk holds Postgres data, Ollama models (~1–2 GB), Docker images (~5–10 GB), and
extracted renditions metadata. File **content** lives in S3, not on the host.

---

## 4. AWS — step by step

### 4.1 Create the S3 bucket + credentials
```sh
aws s3api create-bucket --bucket my-fileengine --region us-east-1
# Block public access (content is served via the app, never directly):
aws s3api put-public-access-block --bucket my-fileengine \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```
Create a least-privilege IAM user (or instance role) scoped to this bucket:
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:ListBucket"],
    "Resource": ["arn:aws:s3:::my-fileengine","arn:aws:s3:::my-fileengine/*"]
  }]
}
```
Note the access key/secret → `S3_ACCESS_KEY` / `S3_SECRET_KEY`. For real AWS S3 set
`S3_ENDPOINT=https://s3.<region>.amazonaws.com`, `S3_REGION=<region>`,
`S3_PATH_STYLE=false` (AWS uses virtual-host style).

> **Prefer an instance role.** Attach the policy to the EC2 instance role instead
> of static keys where possible; the same role then also covers Route 53 DNS-01
> (below).

### 4.2 Launch the EC2 instance
- **AMI:** any current Linux with Docker (the stack is distro-agnostic; the images
  are self-contained). Amazon Linux 2023, Ubuntu 22.04+, or Fedora all work.
- **Type:** `t3.xlarge` (4 vCPU/16 GB) minimum; `m6i.2xlarge` (8/32) recommended.
- **Storage:** 100 GB gp3 EBS.
- **Security group (inbound):** `443/tcp` and `80/tcp` from `0.0.0.0/0` (80 is for
  HTTP→HTTPS redirect and ACME if you ever use HTTP-01); `22/tcp` from **your IP
  only**. No other ports — all datastores stay internal to the host.

### 4.3 Wildcard DNS (Route 53)
In the hosted zone for your domain, add an **A record** (or A-ALIAS):
```
*.fileengine.example.com   A   <EC2 public IP or EIP>
```
Use an **Elastic IP** so the address survives instance restarts. Verify:
```sh
dig +short anything.fileengine.example.com   # → your IP
```

### 4.4 Install Docker + compose
```sh
# Amazon Linux 2023
sudo dnf -y install docker && sudo systemctl enable --now docker
sudo usermod -aG docker $USER && newgrp docker
# Compose plugin:
DOCKER_CONFIG=${DOCKER_CONFIG:-$HOME/.docker}; mkdir -p $DOCKER_CONFIG/cli-plugins
curl -sSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o $DOCKER_CONFIG/cli-plugins/docker-compose && chmod +x $DOCKER_CONFIG/cli-plugins/docker-compose
docker compose version
```

### 4.5 Get the stack onto the host
Two options:

**(a) Build on the host** (needs the source repos as siblings of `docker_unified/`,
plus build tooling — Node for the SPA, rpm/cmake for the C++ RPMs). Clone all repos
into one parent directory, then build (§5).

**(b) Ship prebuilt images (recommended).** Build once in CI / a build box, push to
a registry (ECR), and on the host only pull + `up`:
```sh
# build box: after `make build` + `docker compose build` (see §5)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <acct>.dkr.ecr.us-east-1.amazonaws.com
for s in core http-bridge webdav-bridge csai nginx mcp base; do
  docker tag fileengine-$s:1.1.0 <acct>.dkr.ecr.us-east-1.amazonaws.com/fileengine-$s:1.1.0
  docker push <acct>.dkr.ecr.us-east-1.amazonaws.com/fileengine-$s:1.1.0
done
# host: copy docker_unified/ (compose + .env + init/ + images/nginx assets), set the
# image: refs to the ECR repo (or keep names and `docker pull` + retag), then up.
```

### 4.6 Configure `.env`
```sh
cd docker_unified
cp .env.example .env && $EDITOR .env
```
Set at minimum:
- `BASE_DOMAIN=fileengine.example.com`
- `TLS_MODE=letsencrypt-dns`, `LE_DNS_PROVIDER=route53`, `LE_EMAIL=you@example.com`
- `S3_ENDPOINT`/`S3_REGION`/`S3_BUCKET`/`S3_ACCESS_KEY`/`S3_SECRET_KEY`,
  `S3_PATH_STYLE=false` (real AWS S3)
- `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `LDAP_BIND_PASSWORD`,
  `LDAP_ADMIN_EMAIL`/`LDAP_ADMIN_PASSWORD`
- **`AT_REST_KEY`** — a stable 32-byte secret. **Back it up; losing it makes stored
  content unreadable.** Generate: `openssl rand -hex 16`.
- AI chat: `CSAI_CHAT_PROVIDER` + key (e.g. `anthropic` + `ANTHROPIC_API_KEY`, or
  `openai-compatible` + `CSAI_CHAT_BASE_URL`/`CSAI_CHAT_MODEL`/`CSAI_CHAT_API_KEY`).
  Embeddings default to the bundled Ollama (no key).
- *(optional)* Web search: `CSAI_WEB_SEARCH_ENABLED=true` (needs a tool-capable chat
  provider).

**Never commit `.env`.** It is gitignored and holds every secret.

### 4.7 Route 53 DNS-01 credentials (TLS)
certbot's route53 plugin needs Route 53 access for the wildcard cert. Either:
- **Instance role (preferred):** attach a policy allowing
  `route53:ListHostedZones`, `route53:GetChange`, and
  `route53:ChangeResourceRecordSets` on your zone — no keys in `.env`.
- **Static keys:** put `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (+ `AWS_REGION`)
  in the environment for the nginx service.

### 4.8 Build (if building on host) and bring up
```sh
make build BASE_DOMAIN=fileengine.example.com   # RPMs (events ON) + subdomain-aware SPA + staged sources
make base-image                                 # shared Fedora base
docker compose build                            # service images
docker compose up -d                            # start everything
```
First start runs the one-shots automatically: `db-init` (CSAI DB + pgvector/pg_trgm
extensions), `ldap-init` (directory tree + seeded admin), `ollama-init` (pull
`nomic-embed-text`). Watch them finish:
```sh
docker compose ps
docker compose logs -f db-init ldap-init ollama-init
```

### 4.9 Verify
```sh
docker compose ps                       # all services healthy
curl -kI https://default.fileengine.example.com/        # SPA
curl -k  https://default.fileengine.example.com/api/healthz   # http-bridge
curl -k  https://default.fileengine.example.com/csai/healthz  # csai
```
Then browse `https://default.fileengine.example.com` and log in as
`LDAP_ADMIN_EMAIL` / `LDAP_ADMIN_PASSWORD` (the seeded `default` tenant admin).

### 4.10 Create tenants & users
```sh
scripts/new-tenant.sh acme --admin admin@acme.example.com
```
DNS already wildcards, so `acme.fileengine.example.com` resolves immediately; the
core/CSAI provision the tenant's DB schemas + storage on first access. Manage
users/roles in LDAP (389-ds Cockpit console or `ldapmodify`) — see
[`LDAP_REFERENCE.md`](LDAP_REFERENCE.md) and [`ADMINISTRATOR.md`](ADMINISTRATOR.md).

---

## 5. Build pipeline reference (`make`)

`make build` produces the artifacts the images consume, from each repo's committed
HEAD:
- **RPMs** (core built with Redis **events ON**, http-bridge, webdav-bridge) →
  `rpms/fileengine/`.
- **SPA** built subdomain-aware (`BASE_DOMAIN=…`) → `images/nginx/spa/`.
- **CSAI + MCP source** staged → `images/{csai,mcp}/build-src/`; CSAI migrations →
  `init/migrations/`.

Then `make base-image` + `docker compose build` bake the images. Component RPM
versions are pinned in the `Makefile` (`CORE_VERSION`, `HTTP_VERSION`,
`WEBDAV_VERSION`) and are **independent** of the stack image `VERSION`.

---

## 6. Other cloud providers

The stack is **portable** — any provider that gives you (1) a Linux VM with Docker,
(2) an S3-compatible bucket, and (3) wildcard DNS works. Only the three provider
specifics below change.

### Google Cloud (GCP)
- **VM:** Compute Engine `e2-standard-8` (8/32). Firewall: allow `tcp:80,443`.
- **Object store:** Cloud Storage with **S3 interoperability** (HMAC keys) →
  `S3_ENDPOINT=https://storage.googleapis.com`, `S3_PATH_STYLE=false`,
  HMAC key/secret as `S3_ACCESS_KEY`/`S3_SECRET_KEY`. (Or run MinIO.)
- **DNS / TLS:** Cloud DNS wildcard `*.<base>`. For certbot DNS-01 use
  `LE_DNS_PROVIDER=cloudflare` if your domain is on Cloudflare, otherwise use
  `TLS_MODE=byo` (terminate at a GCP LB / upload certs) or `letsencrypt-manual`.

### Microsoft Azure
- **VM:** `Standard_D8s_v5`. NSG: allow `80`,`443`.
- **Object store:** Azure Blob has no native S3 API — run **MinIO** (gateway/standalone
  on a data disk) and point `S3_*` at it, or use any S3-compatible store.
- **DNS / TLS:** Azure DNS wildcard; `TLS_MODE=byo` with an App Gateway/Key Vault
  cert, or `letsencrypt-manual`.

### DigitalOcean / Hetzner / generic VPS
- **VM:** 8 vCPU / 32 GB droplet/server. Open `80`,`443` in the cloud firewall.
- **Object store:** DO **Spaces** (S3-compatible): `S3_ENDPOINT=https://<region>.digitaloceanspaces.com`,
  `S3_PATH_STYLE=false`. Hetzner: use its S3 object storage or MinIO.
- **DNS / TLS:** put the domain on **Cloudflare**, set `LE_DNS_PROVIDER=cloudflare`
  and mount `/etc/letsencrypt/dns.ini` with the API token — fully automated wildcard
  certs, provider-agnostic.

### Any host (provider-neutral)
1. Linux + Docker + compose. 2. An S3-compatible bucket + keys. 3. Wildcard DNS
`*.<base> → host IP`. Then §4.6–4.9 verbatim. For TLS without DNS-01 automation use
`TLS_MODE=byo` (mount `fullchain.pem`+`privkey.pem`) or `letsencrypt-manual`.

---

## 7. Production hardening (Model B — externalized datastores)

Move stateful services to managed equivalents for HA, backups, and patching. Each
is a drop-in via env + removing the bundled service from compose:

| Bundled | Managed swap | How |
|---|---|---|
| `postgres` | RDS / Cloud SQL / Azure DB **for Postgres with pgvector** | Point `CSAI_PG_HOST`/`CSAI_PG_*` **and** the core's `FILEENGINE_PG_*` at the endpoint; create `CORE_DB` + `CSAI_DB`; ensure the `vector` + `pg_trgm` extensions are enabled. Remove the `postgres` service + `db-init` (run its SQL once against the managed DB). |
| `redis` | ElastiCache / Memorystore | Set `FILEENGINE_REDIS_HOST`/`_PORT`/`_PASSWORD` (core **and** CSAI). Remove the bundled `redis`. |
| `ldap` | Existing 389-ds / AD / any LDAP | Set `LDAP_ENDPOINT` + bind/base envs to your directory; keep the tenant/role group conventions (`LDAP_REFERENCE.md`). Remove `ldap`/`ldap-init`. |
| `ollama` | GPU node or hosted embeddings | Repoint `CSAI_EMBEDDING_BASE_URL` (and switch provider/model if hosted). Keep `CSAI_EMBEDDING_DIMENSION` matched to the model. |
| S3 | already external | — |

Other hardening:
- **Secrets:** source `.env` values from AWS Secrets Manager / SSM / Vault rather than
  a plaintext file; never bake secrets into images.
- **`AT_REST_KEY`:** store in a secrets manager; rotating it requires re-encrypting
  stored content — treat as long-lived.
- **TLS:** prefer `letsencrypt-dns` (auto-renew) or terminate at a managed LB (`byo`).
- **Backups:** schedule `backup/backup.sh` (dumps Postgres + LDAP + `.env`) to S3;
  enable **bucket versioning/replication** for file content. Test restores.
- **Updates:** rebuild images from tagged releases, `docker compose pull/up -d`; the
  one-shots are idempotent. Pin image tags (avoid `latest`).
- **Monitoring:** scrape `docker compose ps` health + the `/healthz`/`/readyz`
  endpoints; ship stdout logs to CloudWatch/Cloud Logging.
- **Scaling:** `csai-worker` is the ingest workhorse — scale it horizontally
  (`docker compose up -d --scale csai-worker=N`) for large corpora; it consumes the
  shared Redis event stream.

---

## 8. Security checklist

- [ ] Security group / firewall exposes **only 80 + 443** (and SSH from your IP).
- [ ] S3 bucket blocks all public access; app holds least-privilege keys (or role).
- [ ] `TLS_MODE` is a real cert mode (**never `none`** in production).
- [ ] All `*_PASSWORD`, `*_API_KEY`, and `AT_REST_KEY` are strong and unique.
- [ ] `AT_REST_KEY` and `.env` are backed up to a secrets manager.
- [ ] `FILEENGINE_ENCRYPT_DATA=true` (at-rest encryption on).
- [ ] `MCP_READ_ONLY` / `MCP_ALLOW_DELETE` set to your agent policy.
- [ ] Elastic/static IP so wildcard DNS stays valid across restarts.
- [ ] Postgres/Redis/LDAP are **not** published on host ports (compose keeps them internal).

---

## 9. Troubleshooting

| Symptom | Check |
|---|---|
| SPA loads but login/chat fails | `CSAI_BRIDGE_URL` must reach http-bridge; `docker compose logs http-bridge csai-app`. |
| Chat: "No module named 'anthropic'" | `CSAI_CHAT_PROVIDER=anthropic` needs `ANTHROPIC_API_KEY`, or switch to `openai-compatible`. |
| Cert issuance fails | DNS-01 needs provider creds (Route 53 role / Cloudflare token); wildcard **can't** use HTTP-01. `docker compose exec nginx obtain-cert.sh`. |
| Tenant subdomain 404 / no route | Wildcard `*.<base>` A record present? Tenant name has no hyphen? |
| Search/chat misses a document | Indexing covers all content (`CSAI_INDEX_BYPASS_ACL=true`); ACLs apply at retrieval. Check `csai-worker` logs; re-run a reconcile sweep. |
| `db-init`/`ldap-init` errored | They're idempotent — read logs, fix `.env`, `docker compose up -d` to re-run. |

See [`ADMINISTRATOR.md`](ADMINISTRATOR.md) for deeper day-2 procedures and
[`backup/README.md`](backup/README.md) for backup/restore.
