# FileEngine BCF-API subservice

The **BCF (BIM Collaboration Format) protocol door** for FileEngine — a
Python/FastAPI service that lets external AEC tools (Revit, Navisworks, Solibri,
BIMcollab) collaborate live against FileEngine over **BCF-API 2.1**.

It implements Phase F / §12 of `frontend/design_documents/XEOKIT_UPGRADE_MARKUP_BCF_PLAN.md`.
It is **not** a second issue store: topics and comments live in the discussion
substrate (reached through the shared `comment_store` interface); this service owns
only the BCF *projection* tables (`bcf_project`, `bcf_topic`, `bcf_viewpoint`,
`bcf_guid_map`) in the same per-tenant schema. "Many doors, one core."

## Status

Implemented:
- App factory + config (env-driven, `FILEENGINE_*` shared names, `BCF_*` knobs).
- Loopback health endpoints (`/healthz`, `/readyz`) with the monitoring IP allowlist.
- BCF-API **discovery**: `GET /bcf/versions`, `GET /bcf/{v}/auth` (OAuth 2.0 discovery
  → ldap_manager / Phase 1.7), `GET /bcf/{v}/current-user`.
- **Bearer auth** (self-contained HS256, pinned; shared `FILEENGINE_JWT_SECRET`) —
  every data route acts under the caller's identity.
- **BCF-XML round-trip** (Phase E / §11): the `.bcfzip` codec + `POST
  /bcf/{v}/bcf-xml/{export,import}`.
- **Foundational BCF-API data endpoints** (Phase F / §12): projects (+ extensions),
  topics, comments, and viewpoints (+ snapshot) CRUD — over an in-memory store.
- The per-tenant BCF projection **DDL** (`store.tenant_ddl`).

Pending:
- Swap the in-memory `BcfStore` for the production split (§13): a psycopg BCF
  projection store + the shared `comment_store` extracted from the discussion
  service, so topic/comment writes go through one guarded path (ACL/FTS/mentions/
  events) and fan out live. Persist BCF-XML imports (upsert-by-guid).
- OAuth2/JWKS bearer verification (beyond the shared HS256 secret); defusedxml
  hardening of `.bcfzip` parsing; extended endpoints (document_references,
  related_topics, events); the SPA Import/Export BCF action.

## Run

```bash
pip install -e .[dev]
cp .env.example .env         # fill FILEENGINE_JWT_SECRET etc.
bcf-service                  # uvicorn on 127.0.0.1:8098
# or: uvicorn bcf_service.app:create_app --factory --port 8098
```

## Test

```bash
pip install -e .[dev]
pytest                       # hermetic — no DB/core/LDAP needed
```
