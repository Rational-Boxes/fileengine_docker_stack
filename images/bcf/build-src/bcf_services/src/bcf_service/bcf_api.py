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

"""BCF-API HTTP surface (buildingSMART BCF-API 2.1).

Scaffold status: the **discovery** endpoints — the minimum a BCF Manager hits
before authenticating — are implemented; the authenticated data endpoints
(projects / topics / comments / viewpoints, §12) return 501 until Phase F wires
the shared ``comment_store`` + BCF projection tables + OAuth verification.

Versioned under ``/bcf/{version}/…`` so a ``/bcf/3.0/`` OpenCDE Foundation tier
can layer on later (§12). ``GET /bcf/versions`` is unversioned.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .auth import Identity, current_identity
from .config import SUPPORTED_BCF_VERSIONS

router = APIRouter(prefix="/bcf")


def _check_version(version: str) -> None:
    if version not in SUPPORTED_BCF_VERSIONS:
        raise HTTPException(status_code=404, detail=f"Unsupported BCF version: {version}")


@router.get("/versions")
def versions() -> dict:
    """Advertise the BCF-API versions this server speaks (buildingSMART /versions)."""
    return {
        "versions": [
            {"version_id": v, "detailed_version": f"BCF-API {v}"} for v in SUPPORTED_BCF_VERSIONS
        ]
    }


@router.get("/{version}/auth")
def auth(version: str, request: Request) -> dict:
    """OAuth 2.0 discovery (buildingSMART /auth). Points desktop tools at the
    Phase 1.7 authorization server (ldap_manager). HTTP Basic is deliberately not
    offered — bearer/OAuth only."""
    _check_version(version)
    cfg = request.app.state.config
    return {
        "oauth2_auth_url": cfg.oauth_auth_url,
        "oauth2_token_url": cfg.oauth_token_url,
        "http_basic_supported": False,
        "supported_oauth2_flows": ["authorization_code_grant"],
    }


@router.get("/{version}/current-user")
def current_user(version: str, ident: Identity = Depends(current_identity)) -> dict:
    """Identity of the authenticated bearer (buildingSMART /current-user)."""
    _check_version(version)
    return {"id": ident.user, "name": ident.user}
