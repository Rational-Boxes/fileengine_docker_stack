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

"""BCF-XML import/export endpoints (Phase E / §11).

Thin HTTP wrappers over the ``bcf_xml`` codec:

- ``POST /bcf/{version}/bcf-xml/export`` — body ``{"topics": [...]}`` (issue dicts;
  viewpoint snapshots as base64) → a ``.bcfzip`` download.
- ``POST /bcf/{version}/bcf-xml/import`` — a ``.bcfzip`` body → the decoded topics
  as JSON (snapshots re-emitted as base64). This is decode-only; **persisting**
  imported topics (upsert-by-guid into the discussion substrate + BCF projection)
  is Phase F, where the shared ``comment_store`` lands.
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .auth import Identity, current_identity
from .bcf_xml import export_bcfzip, import_bcfzip
from .config import SUPPORTED_BCF_VERSIONS

router = APIRouter(prefix="/bcf")


def _check_version(version: str) -> None:
    if version not in SUPPORTED_BCF_VERSIONS:
        raise HTTPException(status_code=404, detail=f"Unsupported BCF version: {version}")


@router.post("/{version}/bcf-xml/export")
async def bcf_xml_export(version: str, request: Request,
                         ident: Identity = Depends(current_identity)) -> Response:
    _check_version(version)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    topics = body.get("topics") or []
    if not isinstance(topics, list):
        raise HTTPException(status_code=400, detail="'topics' must be a list")

    # GUIDs are what the archive is LAID OUT BY — a topic becomes the folder
    # {guid}/ and a viewpoint the file {guid}.bcfv — so a missing one is not a
    # blank field, it is an un-buildable archive. The serializer indexes them
    # directly and raised KeyError, which surfaced as a bare 500 on a request the
    # client got wrong. Say which topic and which field, the way the import side
    # already answers 400 for an archive it cannot read.
    for i, t in enumerate(topics):
        if not isinstance(t, dict):
            raise HTTPException(status_code=400, detail=f"topics[{i}] must be an object")
        if not t.get("guid"):
            raise HTTPException(status_code=400, detail=f"topics[{i}] is missing 'guid'")
        for j, v in enumerate(t.get("viewpoints") or []):
            if not isinstance(v, dict) or not v.get("guid"):
                raise HTTPException(
                    status_code=400,
                    detail=f"topics[{i}].viewpoints[{j}] is missing 'guid'")

    # Snapshots arrive base64-encoded in JSON; decode to bytes for the archive.
    for t in topics:
        for v in t.get("viewpoints") or []:
            b64 = v.pop("snapshot_b64", None)
            try:
                v["snapshot"] = base64.b64decode(b64) if b64 else None
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail=f"viewpoint {v.get('guid')}: 'snapshot_b64' is not valid base64")
    try:
        archive = export_bcfzip(topics, version=version)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing required field: {e.args[0]}")
    return Response(
        content=archive,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="issues.bcfzip"'},
    )


@router.post("/{version}/bcf-xml/import")
async def bcf_xml_import(version: str, request: Request,
                         ident: Identity = Depends(current_identity)) -> JSONResponse:
    _check_version(version)
    data = await request.body()
    try:
        topics = import_bcfzip(data)
    except Exception:
        return JSONResponse({"error": "Not a valid .bcfzip archive"}, status_code=400)
    # Re-emit snapshot bytes as base64 so the decode preview is JSON-serializable.
    for t in topics:
        for v in t.get("viewpoints") or []:
            snap = v.pop("snapshot", None)
            v["snapshot_b64"] = base64.b64encode(snap).decode("ascii") if snap else None
    return JSONResponse({"topics": topics, "persisted": False})  # persistence is Phase F
