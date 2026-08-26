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

"""Issue storage for the BCF-API (Phase F / §12).

``BcfStore`` is the interface the endpoints use. Per §12/§13 the *production* impl
is a thin adapter: topic + comment writes go through the shared ``comment_store``
(the discussion substrate — one guarded write path for ACLs / FTS / mentions /
events), while the BCF *projection* (issue facet, extra viewpoints, project↔folder
+ extensions, ``bcf_guid ↔ thread_id`` map) lives in this service's tables. That
split is a follow-on; the concrete store here is a dependency-free **in-memory**
implementation so the whole BCF-API surface is exercised end-to-end in tests and
runs for local dev.

All ids are UUID strings (BCF Topic/Comment/Viewpoint GUIDs). ``Component.IfcGuid``
inside a viewpoint stays the native 22-char GlobalId (§16) — that lives in the
viewpoint JSON, untouched here.
"""
from __future__ import annotations

import datetime as _dt
import uuid
from typing import List, Optional

# The extensions vocabulary a strict BCF Manager needs populated (§12) or its
# dropdowns come up empty. Seeded per project; editable later.
DEFAULT_EXTENSIONS = {
    "topic_type": ["Issue", "Clash", "Inquiry", "Remark"],
    "topic_status": ["Open", "In Progress", "Closed", "ReOpened"],
    "priority": ["Low", "Normal", "High", "Critical"],
    "topic_label": ["Architecture", "Structure", "MEP"],
    "stage": [],
    "user_id_type": [],
}


def _guid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BcfStore:
    def __init__(self) -> None:
        self._projects: dict = {}    # project_id -> {project_id, name, folder_uid, extensions}
        self._topics: dict = {}      # guid -> topic dict
        self._comments: dict = {}    # guid -> comment dict
        self._viewpoints: dict = {}  # guid -> {guid, topic_guid, viewpoint, snapshot}

    # -- projects (map to FileEngine folders) --------------------------------
    def upsert_project(self, project_id: str, *, name: str = "", folder_uid: str = "") -> dict:
        p = self._projects.get(project_id) or {"project_id": project_id, "extensions": dict(DEFAULT_EXTENSIONS)}
        p["name"] = name or p.get("name", "")
        p["folder_uid"] = folder_uid or p.get("folder_uid", "")
        self._projects[project_id] = p
        return p

    def list_projects(self) -> List[dict]:
        return list(self._projects.values())

    def get_project(self, project_id: str) -> Optional[dict]:
        return self._projects.get(project_id)

    def extensions(self, project_id: str) -> Optional[dict]:
        p = self._projects.get(project_id)
        return p["extensions"] if p else None

    # -- topics (thread + BCF issue facet) -----------------------------------
    def list_topics(self, project_id: str) -> List[dict]:
        return [t for t in self._topics.values() if t["project_id"] == project_id]

    def create_topic(self, project_id: str, author: str, data: dict) -> dict:
        guid = data.get("guid") or _guid()
        now = _now()
        topic = {
            "guid": guid,
            "project_id": project_id,
            "title": data.get("title", ""),
            "topic_type": data.get("topic_type", "Issue"),
            "topic_status": data.get("topic_status", "Open"),
            "priority": data.get("priority"),
            "labels": data.get("labels") or [],
            "assigned_to": data.get("assigned_to"),
            "stage": data.get("stage"),
            "due_date": data.get("due_date"),
            "creation_date": now,
            "creation_author": author,
            "modified_date": now,
            "modified_author": author,
        }
        self._topics[guid] = topic
        return topic

    def get_topic(self, guid: str) -> Optional[dict]:
        return self._topics.get(guid)

    def update_topic(self, guid: str, author: str, data: dict) -> Optional[dict]:
        t = self._topics.get(guid)
        if not t:
            return None
        for k in ("title", "topic_type", "topic_status", "priority", "labels", "assigned_to", "stage", "due_date"):
            if k in data:
                t[k] = data[k]
        t["modified_date"] = _now()
        t["modified_author"] = author
        return t

    def delete_topic(self, guid: str) -> bool:
        if guid not in self._topics:
            return False
        del self._topics[guid]
        for cid in [c for c, v in self._comments.items() if v["topic_guid"] == guid]:
            del self._comments[cid]
        for vid in [v for v, x in self._viewpoints.items() if x["topic_guid"] == guid]:
            del self._viewpoints[vid]
        return True

    # -- comments ------------------------------------------------------------
    def list_comments(self, topic_guid: str) -> List[dict]:
        return [c for c in self._comments.values() if c["topic_guid"] == topic_guid]

    def add_comment(self, topic_guid: str, author: str, data: dict) -> Optional[dict]:
        if topic_guid not in self._topics:
            return None
        guid = data.get("guid") or _guid()
        now = _now()
        comment = {
            "guid": guid,
            "topic_guid": topic_guid,
            "date": now,
            "author": author,
            "comment": data.get("comment", ""),
            "viewpoint_guid": data.get("viewpoint_guid"),
            "modified_date": now,
            "modified_author": author,
        }
        self._comments[guid] = comment
        return comment

    def get_comment(self, guid: str) -> Optional[dict]:
        return self._comments.get(guid)

    def update_comment(self, guid: str, author: str, data: dict) -> Optional[dict]:
        c = self._comments.get(guid)
        if not c:
            return None
        if "comment" in data:
            c["comment"] = data["comment"]
        if "viewpoint_guid" in data:
            c["viewpoint_guid"] = data["viewpoint_guid"]
        c["modified_date"] = _now()
        c["modified_author"] = author
        return c

    def delete_comment(self, guid: str) -> bool:
        return self._comments.pop(guid, None) is not None

    # -- viewpoints ----------------------------------------------------------
    def list_viewpoints(self, topic_guid: str) -> List[dict]:
        return [v for v in self._viewpoints.values() if v["topic_guid"] == topic_guid]

    def add_viewpoint(self, topic_guid: str, data: dict, snapshot: Optional[bytes] = None) -> Optional[dict]:
        if topic_guid not in self._topics:
            return None
        guid = data.get("guid") or _guid()
        vp = {"guid": guid, "topic_guid": topic_guid,
              "viewpoint": data.get("viewpoint") or {k: v for k, v in data.items() if k != "guid"},
              "snapshot": snapshot}
        self._viewpoints[guid] = vp
        return vp

    def get_viewpoint(self, guid: str) -> Optional[dict]:
        return self._viewpoints.get(guid)

    def delete_viewpoint(self, guid: str) -> bool:
        return self._viewpoints.pop(guid, None) is not None
