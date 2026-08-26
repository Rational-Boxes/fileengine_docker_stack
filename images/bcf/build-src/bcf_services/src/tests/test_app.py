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

"""Hermetic tests for the BCF-API scaffold — no DB / core / LDAP.

Exercise the discovery surface a BCF Manager hits before authenticating, the
version guard, the 501 markers on the not-yet-implemented data endpoints, and the
loopback monitoring allowlist.
"""
from fastapi.testclient import TestClient

from bcf_service.app import build_app
from bcf_service.config import Config


def _client(**env) -> TestClient:
    return TestClient(build_app(Config()))


def test_healthz_ok():
    c = TestClient(build_app(Config()))
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["service"] == "bcf_service"


def test_versions_advertises_2_1():
    c = TestClient(build_app(Config()))
    r = c.get("/bcf/versions")
    assert r.status_code == 200
    ids = [v["version_id"] for v in r.json()["versions"]]
    assert "2.1" in ids


def test_auth_discovery_points_at_ldap_manager(monkeypatch):
    monkeypatch.setenv("LDAP_MANAGER_URL", "http://idm.example:8093")
    c = TestClient(build_app(Config()))
    r = c.get("/bcf/2.1/auth")
    assert r.status_code == 200
    body = r.json()
    assert body["oauth2_auth_url"] == "http://idm.example:8093/oauth/authorize"
    assert body["oauth2_token_url"] == "http://idm.example:8093/oauth/token"
    assert body["http_basic_supported"] is False  # bearer/OAuth only


def test_unsupported_version_404():
    c = TestClient(build_app(Config()))
    assert c.get("/bcf/9.9/auth").status_code == 404


def test_data_endpoints_require_auth():
    # Implemented in Phase F — now bearer-gated (401 without a token, not 501).
    c = TestClient(build_app(Config()))
    assert c.get("/bcf/2.1/current-user").status_code == 401
    assert c.get("/bcf/2.1/projects").status_code == 401


def test_monitoring_allowlist_blocks_non_listed(monkeypatch):
    # With an allowlist that excludes the TestClient's host, monitoring is refused.
    monkeypatch.setenv("FILEENGINE_MONITORING_ALLOW_IPS", "10.0.0.1")
    c = TestClient(build_app(Config()))
    assert c.get("/healthz").status_code == 403
    # A non-monitoring path is unaffected by the allowlist.
    assert c.get("/bcf/versions").status_code == 200
