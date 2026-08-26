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

"""Foundational BCF-API 2.1 data endpoints (Phase F / §12).

Projects → Topics → { Comments, Viewpoints }, under ``/bcf/{version}/projects/…``.
The minimum a BCF Manager needs to log in and sync issues. Every route is bearer-
authenticated and acts under the caller's identity; reads/writes go through the
injected ``BcfStore`` (the in-memory dev/test impl today; the discussion-backed
``comment_store`` + BCF projection tables in production, §13).
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from .auth import Identity, current_identity
from .config import SUPPORTED_BCF_VERSIONS
from .stores import BcfStore

router = APIRouter(prefix="/bcf")

_TOPIC_FIELDS = ("guid", "topic_type", "topic_status", "title", "priority", "labels",
                 "assigned_to", "stage", "due_date", "creation_date", "creation_author",
                 "modified_date", "modified_author")
_COMMENT_FIELDS = ("guid", "date", "author", "comment", "topic_guid", "viewpoint_guid",
                   "modified_date", "modified_author")


def _check_version(version: str) -> None:
    if version not in SUPPORTED_BCF_VERSIONS:
        raise HTTPException(status_code=404, detail=f"Unsupported BCF version: {version}")


def _store(request: Request) -> BcfStore:
    return request.app.state.store


def _topic_json(t: dict) -> dict:
    return {k: t.get(k) for k in _TOPIC_FIELDS}


def _comment_json(c: dict) -> dict:
    return {k: c.get(k) for k in _COMMENT_FIELDS}


def _viewpoint_json(v: dict) -> dict:
    out = dict(v.get("viewpoint") or {})
    out["guid"] = v["guid"]
    if v.get("snapshot"):
        out["snapshot"] = {"snapshot_type": "png"}  # bytes fetched via …/snapshot
    return out


def _project_or_404(store: BcfStore, project_id: str) -> dict:
    p = store.get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="project not found")
    return p


def _topic_or_404(store: BcfStore, project_id: str, guid: str) -> dict:
    t = store.get_topic(guid)
    if not t or t["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="topic not found")
    return t


# --- projects --------------------------------------------------------------
@router.get("/{version}/projects")
def list_projects(version: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    return [{"project_id": p["project_id"], "name": p.get("name", "")} for p in _store(request).list_projects()]


@router.get("/{version}/projects/{project_id}")
def get_project(version: str, project_id: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    p = _project_or_404(_store(request), project_id)
    return {"project_id": p["project_id"], "name": p.get("name", "")}


@router.get("/{version}/projects/{project_id}/extensions")
def get_extensions(version: str, project_id: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _project_or_404(_store(request), project_id)
    return _store(request).extensions(project_id)


# --- topics ----------------------------------------------------------------
@router.get("/{version}/projects/{project_id}/topics")
def list_topics(version: str, project_id: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _project_or_404(_store(request), project_id)
    return [_topic_json(t) for t in _store(request).list_topics(project_id)]


@router.post("/{version}/projects/{project_id}/topics", status_code=201)
async def create_topic(version: str, project_id: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _project_or_404(_store(request), project_id)
    data = await request.json()
    return _topic_json(_store(request).create_topic(project_id, ident.user, data))


@router.get("/{version}/projects/{project_id}/topics/{guid}")
def get_topic(version: str, project_id: str, guid: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    return _topic_json(_topic_or_404(_store(request), project_id, guid))


@router.put("/{version}/projects/{project_id}/topics/{guid}")
async def update_topic(version: str, project_id: str, guid: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _topic_or_404(_store(request), project_id, guid)
    data = await request.json()
    return _topic_json(_store(request).update_topic(guid, ident.user, data))


@router.delete("/{version}/projects/{project_id}/topics/{guid}", status_code=204)
def delete_topic(version: str, project_id: str, guid: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _topic_or_404(_store(request), project_id, guid)
    _store(request).delete_topic(guid)
    return Response(status_code=204)


# --- comments --------------------------------------------------------------
@router.get("/{version}/projects/{project_id}/topics/{guid}/comments")
def list_comments(version: str, project_id: str, guid: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _topic_or_404(_store(request), project_id, guid)
    return [_comment_json(c) for c in _store(request).list_comments(guid)]


@router.post("/{version}/projects/{project_id}/topics/{guid}/comments", status_code=201)
async def create_comment(version: str, project_id: str, guid: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _topic_or_404(_store(request), project_id, guid)
    data = await request.json()
    return _comment_json(_store(request).add_comment(guid, ident.user, data))


@router.put("/{version}/projects/{project_id}/topics/{guid}/comments/{cguid}")
async def update_comment(version: str, project_id: str, guid: str, cguid: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _topic_or_404(_store(request), project_id, guid)
    data = await request.json()
    c = _store(request).update_comment(cguid, ident.user, data)
    if not c:
        raise HTTPException(status_code=404, detail="comment not found")
    return _comment_json(c)


@router.delete("/{version}/projects/{project_id}/topics/{guid}/comments/{cguid}", status_code=204)
def delete_comment(version: str, project_id: str, guid: str, cguid: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _topic_or_404(_store(request), project_id, guid)
    if not _store(request).delete_comment(cguid):
        raise HTTPException(status_code=404, detail="comment not found")
    return Response(status_code=204)


# --- viewpoints ------------------------------------------------------------
@router.get("/{version}/projects/{project_id}/topics/{guid}/viewpoints")
def list_viewpoints(version: str, project_id: str, guid: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _topic_or_404(_store(request), project_id, guid)
    return [_viewpoint_json(v) for v in _store(request).list_viewpoints(guid)]


@router.post("/{version}/projects/{project_id}/topics/{guid}/viewpoints", status_code=201)
async def create_viewpoint(version: str, project_id: str, guid: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _topic_or_404(_store(request), project_id, guid)
    body = await request.json()
    snapshot = None
    snap = body.pop("snapshot", None)
    if isinstance(snap, dict) and snap.get("snapshot_data"):
        try:
            snapshot = base64.b64decode(snap["snapshot_data"])
        except Exception:
            snapshot = None
    v = _store(request).add_viewpoint(guid, {"guid": body.pop("guid", None), "viewpoint": body}, snapshot)
    return _viewpoint_json(v)


@router.get("/{version}/projects/{project_id}/topics/{guid}/viewpoints/{vguid}")
def get_viewpoint(version: str, project_id: str, guid: str, vguid: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _topic_or_404(_store(request), project_id, guid)
    v = _store(request).get_viewpoint(vguid)
    if not v or v["topic_guid"] != guid:
        raise HTTPException(status_code=404, detail="viewpoint not found")
    return _viewpoint_json(v)


@router.get("/{version}/projects/{project_id}/topics/{guid}/viewpoints/{vguid}/snapshot")
def get_snapshot(version: str, project_id: str, guid: str, vguid: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _topic_or_404(_store(request), project_id, guid)
    v = _store(request).get_viewpoint(vguid)
    if not v or v["topic_guid"] != guid or not v.get("snapshot"):
        raise HTTPException(status_code=404, detail="snapshot not found")
    return Response(content=v["snapshot"], media_type="image/png")


@router.delete("/{version}/projects/{project_id}/topics/{guid}/viewpoints/{vguid}", status_code=204)
def delete_viewpoint(version: str, project_id: str, guid: str, vguid: str, request: Request, ident: Identity = Depends(current_identity)):
    _check_version(version)
    _topic_or_404(_store(request), project_id, guid)
    v = _store(request).get_viewpoint(vguid)
    if not v or v["topic_guid"] != guid:
        raise HTTPException(status_code=404, detail="viewpoint not found")
    _store(request).delete_viewpoint(vguid)
    return Response(status_code=204)
