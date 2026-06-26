# Backups

What's stateful in the unified stack, and how to protect it.

| Data | Where | Covered by |
|------|-------|-----------|
| File **content** | the external **S3 bucket** (source of truth) | the object store — see below |
| File **metadata** (files/versions/ACLs/roles/tenants) | Postgres `fileengine` | `backup.sh` |
| **CSAI index** (documents + per-tenant pgvector/FTS) | Postgres `convert_search_ai` | `backup.sh` |
| **Identity** (users/tenants/role groups) | LDAP (389-ds) | `backup.sh` |
| **Secrets** (incl. `AT_REST_KEY`) | `.env` | `backup.sh` (`env.backup`, 0600) |
| TLS certs | `letsencrypt` / `nginxtls` volumes | re-issuable; or snapshot the volume |
| Local cache | `filecache` volume | **not needed** — rehydrates from S3 |

## Run a backup
```sh
backup/backup.sh                 # -> backups/<timestamp>/
BACKUP_DIR=/mnt/backups backup/backup.sh   # custom destination
```
Produces `pg-fileengine.dump`, `pg-convert_search_ai.dump` (custom-format),
`directory.ldif`, `env.backup`, and `MANIFEST.txt`. The stack must be running.

Schedule it from cron, e.g. nightly:
```
15 2 * * *  cd /opt/fileengine && BACKUP_DIR=/mnt/backups backup/backup.sh >> /var/log/fe-backup.log 2>&1
```

## Restore
Bring the stack up (so `db-init`/`ldap-init` create the databases + LDAP suffix),
then:
```sh
backup/restore.sh backups/<timestamp>
```
Postgres is restored with `pg_restore --clean --if-exists`; LDAP entries are
re-added idempotently. File content is **not** restored per-file — it lives in S3.

## The S3 bucket (file content — the source of truth)
The object store is **external and not bundled**, so back it up with the
provider's own tooling — this is the most important data:
- **Enable bucket versioning** (recovers overwrites/deletes) and a sensible
  lifecycle policy.
- **Cross-region / cross-account replication** for DR, or periodic mirroring:
  ```sh
  aws s3 sync   s3://<bucket> s3://<backup-bucket>      # AWS
  mc mirror     src/<bucket>  dst/<bucket>              # MinIO client
  ```
- FileEngine writes objects immutably (no in-place mutation), so versioning +
  replication is sufficient; there is no "consistent snapshot" requirement
  between Postgres and S3 because metadata references immutable object keys.

## ⚠️ `AT_REST_KEY`
Objects are encrypted at rest with `AT_REST_KEY`. **If you lose it, the S3 data
is unrecoverable** even with the bucket intact. Keep it in a secrets manager,
not only in `env.backup`. Restoring metadata + S3 without the original key yields
unreadable files.

## Consistency note
Take the Postgres + LDAP backup together (they're small and fast). The metadata
↔ S3 relationship is eventually consistent and key-immutable, so a metadata
backup slightly newer/older than the bucket is safe: missing objects surface as
unreadable files (re-uploadable), and orphan objects are harmless.
