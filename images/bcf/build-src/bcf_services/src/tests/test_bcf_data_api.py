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

"""Foundational BCF-API data endpoints (Phase F / §12) — end-to-end over the
in-memory store, with an injected identity (no real tokens)."""
import base64

from fastapi.testclient import TestClient

from bcf_service.app import build_app
from bcf_service.auth import Identity
from bcf_service.config import Config
from bcf_service.stores import BcfStore

AUTH = {"Authorization": "Bearer test"}


def _client(store: BcfStore):
    # Inject the store + a fake verifier that returns a fixed identity.
    return TestClient(build_app(Config(), store=store, verify_bearer=lambda t: Identity(user="alice", roles=["users"])))


def test_requires_bearer():
    c = _client(BcfStore())
    assert c.get("/bcf/2.1/projects").status_code == 401  # no header
    # A verifier that rejects the token → 401.
    bad = TestClient(build_app(Config(), store=BcfStore(), verify_bearer=lambda t: None))
    assert bad.get("/bcf/2.1/projects", headers=AUTH).status_code == 401


def test_current_user_and_projects_and_extensions():
    store = BcfStore()
    store.upsert_project("proj1", name="Tower", folder_uid="fold1")
    c = _client(store)
    assert c.get("/bcf/2.1/current-user", headers=AUTH).json()["id"] == "alice"
    projs = c.get("/bcf/2.1/projects", headers=AUTH).json()
    assert projs == [{"project_id": "proj1", "name": "Tower"}]
    ext = c.get("/bcf/2.1/projects/proj1/extensions", headers=AUTH).json()
    assert "Open" in ext["topic_status"] and "Clash" in ext["topic_type"]  # populated (§12)
    assert c.get("/bcf/2.1/projects/nope/extensions", headers=AUTH).status_code == 404


def test_topic_comment_lifecycle():
    store = BcfStore()
    store.upsert_project("proj1", name="Tower")
    c = _client(store)

    # Create a topic.
    r = c.post("/bcf/2.1/projects/proj1/topics", headers=AUTH,
               json={"title": "Duct clash", "topic_type": "Clash", "priority": "High"})
    assert r.status_code == 201
    topic = r.json()
    assert topic["title"] == "Duct clash" and topic["creation_author"] == "alice"
    tg = topic["guid"]

    # It shows up in the list and by guid.
    assert [t["guid"] for t in c.get(f"/bcf/2.1/projects/proj1/topics", headers=AUTH).json()] == [tg]
    assert c.get(f"/bcf/2.1/projects/proj1/topics/{tg}", headers=AUTH).json()["priority"] == "High"

    # Update status.
    r = c.put(f"/bcf/2.1/projects/proj1/topics/{tg}", headers=AUTH, json={"topic_status": "Closed"})
    assert r.json()["topic_status"] == "Closed"

    # Add a comment.
    r = c.post(f"/bcf/2.1/projects/proj1/topics/{tg}/comments", headers=AUTH,
               json={"comment": "Rerouted the duct."})
    assert r.status_code == 201 and r.json()["author"] == "alice"
    cg = r.json()["guid"]
    assert len(c.get(f"/bcf/2.1/projects/proj1/topics/{tg}/comments", headers=AUTH).json()) == 1

    # Delete the comment, then the topic.
    assert c.delete(f"/bcf/2.1/projects/proj1/topics/{tg}/comments/{cg}", headers=AUTH).status_code == 204
    assert c.delete(f"/bcf/2.1/projects/proj1/topics/{tg}", headers=AUTH).status_code == 204
    assert c.get(f"/bcf/2.1/projects/proj1/topics/{tg}", headers=AUTH).status_code == 404


def test_viewpoint_with_snapshot():
    store = BcfStore()
    store.upsert_project("proj1")
    c = _client(store)
    tg = c.post("/bcf/2.1/projects/proj1/topics", headers=AUTH, json={"title": "T"}).json()["guid"]

    # Create a viewpoint carrying a camera + a base64 snapshot.
    r = c.post(f"/bcf/2.1/projects/proj1/topics/{tg}/viewpoints", headers=AUTH, json={
        "perspective_camera": {"field_of_view": 60},
        "snapshot": {"snapshot_type": "png", "snapshot_data": base64.b64encode(b"PNG").decode()},
    })
    assert r.status_code == 201
    vp = r.json()
    vg = vp["guid"]
    assert vp["perspective_camera"]["field_of_view"] == 60
    assert vp["snapshot"]["snapshot_type"] == "png"

    # The binary snapshot is fetchable.
    s = c.get(f"/bcf/2.1/projects/proj1/topics/{tg}/viewpoints/{vg}/snapshot", headers=AUTH)
    assert s.status_code == 200 and s.headers["content-type"] == "image/png" and s.content == b"PNG"

    # Delete it.
    assert c.delete(f"/bcf/2.1/projects/proj1/topics/{tg}/viewpoints/{vg}", headers=AUTH).status_code == 204


def test_topic_scoped_to_its_project():
    store = BcfStore()
    store.upsert_project("proj1")
    store.upsert_project("proj2")
    c = _client(store)
    tg = c.post("/bcf/2.1/projects/proj1/topics", headers=AUTH, json={"title": "T"}).json()["guid"]
    # The same topic guid under a different project 404s (no cross-project leak).
    assert c.get(f"/bcf/2.1/projects/proj2/topics/{tg}", headers=AUTH).status_code == 404
