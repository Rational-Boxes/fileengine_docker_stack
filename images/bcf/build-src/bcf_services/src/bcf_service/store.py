# Copyright (C) 2026 James Hickman
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""BCF projection storage (scaffold).

Per §12/§13 of the plan, this service is an adapter, not a second issue store:

  * Topic + comment writes go through a shared ``comment_store`` interface (to be
    EXTRACTED from the discussion service) so both doors write threads/comments
    through one guarded code path (ACL checks, body_text/FTS, mention extraction,
    event emission). That extraction is a Phase F task; the seam is named here.
  * This service OWNS only the BCF *projection* tables, in the SAME per-tenant
    schema the discussion substrate uses: the issue facet on a thread, a topic's
    extra viewpoints, project↔folder mapping + extensions vocab, and the
    ``bcf_guid ↔ thread_id`` identity map that makes round-trip stable.

Only the DDL is defined at this stage (hermetically testable, like the discussion
service's schema.py). Live connection handling + the CRUD adapter arrive in Phase F.
"""
from __future__ import annotations

import re

_UNSAFE = re.compile(r"[^a-zA-Z0-9_]")


def schema_name(tenant: str | None) -> str:
    """Per-tenant schema name, matching the core / discussion convention."""
    t = (tenant or "").strip()
    if not t:
        return "tenant_default"
    return "tenant_" + _UNSAFE.sub("_", t)


# Additive, idempotent DDL for one tenant's BCF projection tables. Every table is
# BCF-only and keyed back to the discussion substrate by ``thread_id`` / folder uid.
_TENANT_DDL = '''
-- Project ↔ FileEngine folder (§10). All models in the folder are the project's
-- models; `extensions` holds the vocab (types/statuses/priorities/labels/stages)
-- strict tools require to be populated.
CREATE TABLE IF NOT EXISTS "{schema}".bcf_project (
    project_id  TEXT PRIMARY KEY,          -- BCF project GUID
    folder_uid  TEXT NOT NULL,             -- the core folder this project maps to
    name        TEXT NOT NULL DEFAULT '',
    extensions  JSONB NOT NULL DEFAULT '{{}}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The BCF issue facet on a discussion thread (§10). A thread becomes a BCF topic
-- exactly when it gains this row; not every thread is a topic.
CREATE TABLE IF NOT EXISTS "{schema}".bcf_topic (
    thread_id         TEXT PRIMARY KEY,     -- FK to the discussion thread (substrate)
    bcf_guid          TEXT NOT NULL UNIQUE, -- stable topic GUID (round-trip identity)
    project_id        TEXT REFERENCES "{schema}".bcf_project (project_id) ON DELETE SET NULL,
    topic_type        TEXT,
    topic_status      TEXT,
    priority          TEXT,
    assigned_to       TEXT,
    labels            JSONB NOT NULL DEFAULT '[]',
    due_date          TIMESTAMPTZ,
    stage             TEXT,
    server_assigned_id TEXT,                -- BCF 3.0 forward-compat
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bcf_topic_project ON "{schema}".bcf_topic (project_id);

-- A topic's ADDITIONAL viewpoints beyond the thread's anchor viewpoint (§10). The
-- viewpoint JSON is the BCF-2.1 viewpoint shape; snapshot is a core rendition uid.
CREATE TABLE IF NOT EXISTS "{schema}".bcf_viewpoint (
    guid                  TEXT PRIMARY KEY,
    thread_id             TEXT NOT NULL REFERENCES "{schema}".bcf_topic (thread_id) ON DELETE CASCADE,
    viewpoint             JSONB NOT NULL,
    snapshot_rendition_uid TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bcf_viewpoint_thread ON "{schema}".bcf_viewpoint (thread_id);

-- Identity map so re-imports upsert rather than duplicate (§11).
CREATE TABLE IF NOT EXISTS "{schema}".bcf_guid_map (
    bcf_guid   TEXT PRIMARY KEY,
    thread_id  TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
'''


def tenant_ddl(tenant: str) -> str:
    """The idempotent BCF projection DDL for one tenant's schema."""
    return _TENANT_DDL.format(schema=schema_name(tenant))
