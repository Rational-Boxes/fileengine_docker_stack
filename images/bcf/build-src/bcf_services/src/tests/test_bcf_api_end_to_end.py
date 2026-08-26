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

"""End-to-end BCF-API journey (§12/§11) — drives the whole surface the way a real
BCF Manager (BIMcollab / Solibri) does: discovery → auth → project → topic →
comment → viewpoint, then exports the created issue to a ``.bcfzip`` and re-imports
it, proving the data endpoints and the BCF-XML endpoints interoperate on the same
shapes. Runs hermetically over the in-memory store with an injected identity.
"""
import base64

from fastapi.testclient import TestClient

from bcf_service.app import build_app
from bcf_service.auth import Identity
from bcf_service.config import Config
from bcf_service.stores import BcfStore

AUTH = {"Authorization": "Bearer test"}
PNG = b"\x89PNG\r\n\x1a\nsnapshot-bytes"

# A viewpoint as xeokit getViewpoint() emits it (camera + a clip plane + an IFC
# selection) — the shape the frontend POSTs and a BCF Manager expects back.
VIEWPOINT_BODY = {
    "perspective_camera": {
        "camera_view_point": {"x": 1.0, "y": 2.0, "z": 3.0},
        "camera_direction": {"x": 0.0, "y": 0.0, "z": -1.0},
        "camera_up_vector": {"x": 0.0, "y": 1.0, "z": 0.0},
        "field_of_view": 60.0,
    },
    "clipping_planes": [
        {"location": {"x": 0.0, "y": 0.0, "z": 0.0}, "direction": {"x": 1.0, "y": 0.0, "z": 0.0}},
    ],
    "components": {"selection": [{"ifc_guid": "3xY7Uv$abc0000000000w1"}]},
}


def _client(store: BcfStore) -> TestClient:
    return TestClient(build_app(Config(), store=store,
                                verify_bearer=lambda t: Identity(user="alice", roles=["users"])))


def test_bcf_manager_full_journey_data_then_xml_roundtrip():
    store = BcfStore()
    # A project is provisioned server-side from a FileEngine folder (§13).
    store.upsert_project("proj1", name="Institute", folder_uid="fold1")
    c = _client(store)

    # 1. Discovery — what a BCF Manager fetches before authenticating.
    versions = c.get("/bcf/versions").json()["versions"]
    assert "2.1" in [v["version_id"] for v in versions]
    auth = c.get("/bcf/2.1/auth").json()
    assert auth["oauth2_token_url"].endswith("/oauth/token")
    assert auth["http_basic_supported"] is False  # OAuth/Bearer only

    # 2. Auth gate — every data route is bearer-protected.
    assert c.get("/bcf/2.1/current-user").status_code == 401
    assert c.get("/bcf/2.1/projects/proj1/topics", headers=AUTH).status_code == 200

    # 3. Identity + project discovery.
    assert c.get("/bcf/2.1/current-user", headers=AUTH).json()["id"] == "alice"
    assert c.get("/bcf/2.1/projects", headers=AUTH).json() == [{"project_id": "proj1", "name": "Institute"}]
    ext = c.get("/bcf/2.1/projects/proj1/extensions", headers=AUTH).json()
    assert "Open" in ext["topic_status"]

    # 4. Create a topic.
    tg = c.post("/bcf/2.1/projects/proj1/topics", headers=AUTH,
                json={"title": "Duct vs beam", "topic_type": "Clash", "priority": "High"}).json()["guid"]

    # 5. Attach a viewpoint (with a PNG snapshot) and a comment referencing it.
    vp = c.post(f"/bcf/2.1/projects/proj1/topics/{tg}/viewpoints", headers=AUTH, json={
        **VIEWPOINT_BODY,
        "snapshot": {"snapshot_type": "png", "snapshot_data": base64.b64encode(PNG).decode()},
    }).json()
    vg = vp["guid"]
    assert vp["perspective_camera"]["field_of_view"] == 60.0
    cg = c.post(f"/bcf/2.1/projects/proj1/topics/{tg}/comments", headers=AUTH,
                json={"comment": "Rerouted the duct.", "viewpoint_guid": vg}).json()["guid"]

    # 6. Read everything back the way a Manager syncs it.
    topic = c.get(f"/bcf/2.1/projects/proj1/topics/{tg}", headers=AUTH).json()
    assert topic["topic_status"] == "Open" and topic["creation_author"] == "alice"
    comments = c.get(f"/bcf/2.1/projects/proj1/topics/{tg}/comments", headers=AUTH).json()
    assert len(comments) == 1 and comments[0]["viewpoint_guid"] == vg
    snap = c.get(f"/bcf/2.1/projects/proj1/topics/{tg}/viewpoints/{vg}/snapshot", headers=AUTH)
    assert snap.status_code == 200 and snap.headers["content-type"] == "image/png" and snap.content == PNG

    # 7. Update the topic status (BCF Managers close issues over PUT).
    assert c.put(f"/bcf/2.1/projects/proj1/topics/{tg}", headers=AUTH,
                 json={"topic_status": "Closed"}).json()["topic_status"] == "Closed"

    # 8. Export the created issue to a .bcfzip — reshaping the data-API responses
    #    into the export payload exactly as the frontend "Export BCF" path does.
    export_payload = {"topics": [{
        "guid": tg,
        "title": topic["title"],
        "topic_type": topic["topic_type"],
        "topic_status": "Closed",
        "priority": topic["priority"],
        "creation_author": topic["creation_author"],
        "comments": [{"guid": cg, "author": comments[0]["author"],
                      "comment": comments[0]["comment"], "viewpoint_guid": vg}],
        "viewpoints": [{"guid": vg, "viewpoint": VIEWPOINT_BODY,
                        "snapshot_b64": base64.b64encode(PNG).decode()}],
    }]}
    r = c.post("/bcf/2.1/bcf-xml/export", headers=AUTH, json=export_payload)
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    archive = r.content

    # The archive is a real .bcfzip: version marker + the topic's markup folder.
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        names = z.namelist()
    assert "bcf.version" in names and f"{tg}/markup.bcf" in names

    # 9. Re-import the archive → the issue survives the round-trip intact.
    back = c.post("/bcf/2.1/bcf-xml/import", headers=AUTH, content=archive).json()
    assert back["persisted"] is False  # decode-only preview (Phase F persistence is separate)
    imported = back["topics"][0]
    assert imported["guid"] == tg
    assert imported["topic_status"] == "Closed"
    assert imported["comments"][0]["comment"].startswith("Rerouted")
    assert imported["comments"][0]["viewpoint_guid"] == vg
    iv = imported["viewpoints"][0]
    assert iv["guid"] == vg
    assert base64.b64decode(iv["snapshot_b64"]) == PNG  # snapshot bytes survive
    assert iv["viewpoint"]["perspective_camera"]["field_of_view"] == 60.0
    assert iv["viewpoint"]["components"]["selection"][0]["ifc_guid"] == "3xY7Uv$abc0000000000w1"

    # 10. Delete the topic — the store no longer serves it.
    assert c.delete(f"/bcf/2.1/projects/proj1/topics/{tg}", headers=AUTH).status_code == 204
    assert c.get(f"/bcf/2.1/projects/proj1/topics/{tg}", headers=AUTH).status_code == 404


def test_cross_version_and_unknown_ids_are_rejected():
    """The endpoints guard the version and every id boundary a Manager can fumble."""
    store = BcfStore()
    store.upsert_project("proj1", name="Institute")
    c = _client(store)
    tg = c.post("/bcf/2.1/projects/proj1/topics", headers=AUTH, json={"title": "T"}).json()["guid"]

    assert c.get("/bcf/9.9/projects", headers=AUTH).status_code == 404          # bad version
    assert c.get("/bcf/2.1/projects/nope/topics", headers=AUTH).status_code == 404  # unknown project
    assert c.get(f"/bcf/2.1/projects/proj1/topics/{'0' * 8}", headers=AUTH).status_code == 404  # unknown topic
    # A viewpoint guid that isn't under this topic 404s (no cross-topic leak).
    assert c.get(f"/bcf/2.1/projects/proj1/topics/{tg}/viewpoints/deadbeef", headers=AUTH).status_code == 404
