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

"""BCF-XML codec + endpoints (Phase E / §11) — hermetic golden round-trips."""
import base64

from fastapi.testclient import TestClient

from bcf_service.app import build_app
from bcf_service.bcf_xml import (
    bcfv_to_viewpoint_json,
    export_bcfzip,
    import_bcfzip,
    markup_to_topic,
    topic_to_markup,
    viewpoint_json_to_bcfv,
)
from bcf_service.config import Config


# A viewpoint in BCF-API JSON form (as xeokit getViewpoint() emits): camera +
# section plane + selection + a visibility exception, with an IFC component.
VIEWPOINT = {
    "perspective_camera": {
        "camera_view_point": {"x": 1.0, "y": 2.0, "z": 3.0},
        "camera_direction": {"x": 0.0, "y": 0.0, "z": -1.0},
        "camera_up_vector": {"x": 0.0, "y": 1.0, "z": 0.0},
        "field_of_view": 60.0,
    },
    "clipping_planes": [
        {"location": {"x": 0.0, "y": 0.0, "z": 0.0}, "direction": {"x": 1.0, "y": 0.0, "z": 0.0}},
    ],
    "components": {
        "selection": [{"ifc_guid": "3xY7Uv$abc0000000000w1"}],
        "visibility": {"default_visibility": True, "exceptions": [{"ifc_guid": "2aB3Cd$xyz0000000000q9"}]},
    },
}

TOPIC = {
    "guid": "11111111-1111-4111-8111-111111111111",
    "title": "Clash: duct vs beam",
    "topic_type": "Clash",
    "topic_status": "Open",
    "priority": "High",
    "creation_date": "2026-07-23T10:00:00Z",
    "creation_author": "alice",
    "comments": [
        {"guid": "22222222-2222-4222-8222-222222222222", "date": "2026-07-23T10:01:00Z",
         "author": "alice", "comment": "The duct clashes with the beam here.",
         "viewpoint_guid": "33333333-3333-4333-8333-333333333333"},
    ],
    "viewpoints": [
        {"guid": "33333333-3333-4333-8333-333333333333", "viewpoint": VIEWPOINT, "snapshot": b"\x89PNG\r\n\x1a\nfake"},
    ],
}


def test_viewpoint_json_bcfv_roundtrip():
    xml = viewpoint_json_to_bcfv(VIEWPOINT, "vp-guid")
    back = bcfv_to_viewpoint_json(xml)
    pc = back["perspective_camera"]
    assert pc["camera_view_point"] == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert pc["field_of_view"] == 60.0
    assert back["clipping_planes"][0]["direction"] == {"x": 1.0, "y": 0.0, "z": 0.0}
    assert back["components"]["selection"][0]["ifc_guid"] == "3xY7Uv$abc0000000000w1"
    assert back["components"]["visibility"]["default_visibility"] is True
    assert back["components"]["visibility"]["exceptions"][0]["ifc_guid"].endswith("q9")


def test_markup_roundtrip_preserves_guids_and_comments():
    xml = topic_to_markup(TOPIC)
    t = markup_to_topic(xml)
    assert t["guid"] == TOPIC["guid"]
    assert t["topic_type"] == "Clash" and t["topic_status"] == "Open"
    assert t["title"] == TOPIC["title"]
    assert len(t["comments"]) == 1
    assert t["comments"][0]["guid"] == TOPIC["comments"][0]["guid"]
    assert t["comments"][0]["viewpoint_guid"] == TOPIC["viewpoints"][0]["guid"]
    assert t["viewpoints"][0]["guid"] == TOPIC["viewpoints"][0]["guid"]


def test_bcfzip_roundtrip_end_to_end():
    archive = export_bcfzip([TOPIC])
    # A valid zip with the version marker + the topic folder.
    import zipfile
    import io
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        assert "bcf.version" in z.namelist()
        assert f"{TOPIC['guid']}/markup.bcf" in z.namelist()

    got = import_bcfzip(archive)
    assert len(got) == 1
    t = got[0]
    assert t["guid"] == TOPIC["guid"]
    assert t["comments"][0]["comment"].startswith("The duct clashes")
    v = t["viewpoints"][0]
    assert v["guid"] == TOPIC["viewpoints"][0]["guid"]
    assert v["snapshot"] == b"\x89PNG\r\n\x1a\nfake"  # snapshot bytes survive
    assert v["viewpoint"]["perspective_camera"]["field_of_view"] == 60.0  # viewpoint survives


def test_import_invalid_zip_raises():
    import pytest
    with pytest.raises(Exception):
        import_bcfzip(b"this is not a zip")


# --------------------------- endpoints ------------------------------------- #

from bcf_service.auth import Identity  # noqa: E402

AUTH = {"Authorization": "Bearer test"}


def _client() -> TestClient:
    # The BCF-XML endpoints are auth-gated (like the data endpoints); inject a fake
    # bearer verifier so the round-trip tests authenticate.
    return TestClient(build_app(Config(), verify_bearer=lambda t: Identity(user="alice", roles=["users"])))


def test_xml_endpoints_require_auth():
    # No credential → 401 before the handler runs (closes the pre-Phase-F hole).
    anon = TestClient(build_app(Config(), verify_bearer=lambda t: None))
    assert anon.post("/bcf/2.1/bcf-xml/export", json={"topics": []}).status_code == 401
    assert anon.post("/bcf/2.1/bcf-xml/import", content=b"x").status_code == 401


def test_export_accepts_a_bcf_service_credential():
    # Gateway key:secret (Basic, scope "bcf") — a verify_basic returning an identity.
    seen = {}

    def verify_basic(key_id, secret, tenant, source_ip=None):
        seen.update(key_id=key_id, secret=secret, tenant=tenant)
        return Identity(user="svc", tenant=tenant or "default", roles=[])

    c = TestClient(build_app(Config(), verify_basic=verify_basic))
    import base64 as _b64
    basic = _b64.b64encode(b"fek_abc:fesks_xyz").decode()
    r = c.post("/bcf/2.1/bcf-xml/export", json={"topics": []},
               headers={"Authorization": f"Basic {basic}", "X-Tenant": "acme"})
    assert r.status_code == 200
    assert seen == {"key_id": "fek_abc", "secret": "fesks_xyz", "tenant": "acme"}


def test_export_then_import_endpoints_roundtrip():
    c = _client()
    # Export: topics with a base64 snapshot → a .bcfzip download.
    payload = {
        "topics": [{
            **{k: v for k, v in TOPIC.items() if k != "viewpoints"},
            "viewpoints": [{
                "guid": TOPIC["viewpoints"][0]["guid"],
                "viewpoint": VIEWPOINT,
                "snapshot_b64": base64.b64encode(b"PNGDATA").decode("ascii"),
            }],
        }],
    }
    r = c.post("/bcf/2.1/bcf-xml/export", headers=AUTH, json=payload)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    archive = r.content

    # Import the archive back → decoded topics (snapshot re-emitted as base64).
    r2 = c.post("/bcf/2.1/bcf-xml/import", headers=AUTH, content=archive)
    assert r2.status_code == 200
    body = r2.json()
    assert body["persisted"] is False
    t = body["topics"][0]
    assert t["guid"] == TOPIC["guid"]
    v = t["viewpoints"][0]
    assert base64.b64decode(v["snapshot_b64"]) == b"PNGDATA"
    assert v["viewpoint"]["clipping_planes"][0]["location"] == {"x": 0.0, "y": 0.0, "z": 0.0}


def test_export_unsupported_version_404():
    c = _client()
    assert c.post("/bcf/9.9/bcf-xml/export", headers=AUTH, json={"topics": []}).status_code == 404


def test_import_bad_archive_400():
    c = _client()
    assert c.post("/bcf/2.1/bcf-xml/import", headers=AUTH, content=b"garbage").status_code == 400


# --- export input validation -------------------------------------------------
#
# The endpoint takes arbitrary JSON from the client, and GUIDs are what the
# archive is laid out by (a topic becomes the folder {guid}/, a viewpoint the
# file {guid}.bcfv), so a missing one used to escape as a KeyError and a bare
# 500. These pin the 400s.
from fastapi.testclient import TestClient as _TestClient

from bcf_service.app import build_app as _build_app
from bcf_service.auth import Identity as _Identity
from bcf_service.config import Config as _Config

_AUTH = {"Authorization": "Bearer test"}


def _xml_client() -> _TestClient:
    return _TestClient(_build_app(
        _Config(), verify_bearer=lambda t: _Identity(user="alice", roles=["users"])))


def _export(payload):
    return _xml_client().post("/bcf/2.1/bcf-xml/export", json=payload, headers=_AUTH)


def test_export_rejects_a_topic_without_a_guid():
    r = _export({"topics": [{"title": "no guid"}]})
    assert r.status_code == 400
    assert "guid" in r.json()["detail"]
    assert "topics[0]" in r.json()["detail"]


def test_export_rejects_a_viewpoint_without_a_guid():
    r = _export({"topics": [{"guid": "t-1", "viewpoints": [{"viewpoint": {}}]}]})
    assert r.status_code == 400
    assert "viewpoints[0]" in r.json()["detail"]


def test_export_rejects_non_list_topics():
    assert _export({"topics": {"guid": "t-1"}}).status_code == 400


def test_export_rejects_undecodable_snapshot():
    r = _export({"topics": [{"guid": "t-1", "viewpoints": [
        {"guid": "v-1", "snapshot_b64": "!!!not base64!!!"}]}]})
    assert r.status_code == 400
    assert "base64" in r.json()["detail"]


def test_export_still_builds_a_valid_archive():
    """The guard must not have cost the happy path."""
    guid = "11111111-1111-4111-8111-111111111111"
    r = _export({"topics": [{"guid": guid, "title": "Clash at grid B4",
                             "status": "Open"}]})
    assert r.status_code == 200
    assert r.content[:4] == b"PK\x03\x04"
    import io as _io
    import zipfile as _zip
    names = _zip.ZipFile(_io.BytesIO(r.content)).namelist()
    assert "bcf.version" in names
    assert f"{guid}/markup.bcf" in names
