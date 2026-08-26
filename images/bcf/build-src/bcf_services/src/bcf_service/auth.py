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

"""Bearer authentication for the BCF-API (Phase F / §12).

BCF Managers authenticate via OAuth 2.0 (Phase 1.7 / ldap_manager); the tokens are
HS256 JWTs signed with the shared ``FILEENGINE_JWT_SECRET`` (as the bridges/mcp/
discussion issue + verify). Verification is self-contained and pins HS256 —
``alg: none`` and RS/HS-confusion tokens are rejected — mirroring the discussion
service's ``jwt_verify``. The service then acts *under the token's identity*
(impersonation), so every BCF read/write runs through FileEngine's ACLs.

Tests inject ``app.state.verify_bearer`` to supply an identity without minting real
tokens; production wires the config-secret verifier in ``app.build_app``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

from fastapi import HTTPException, Request


@dataclass
class Identity:
    user: str
    tenant: str = "default"
    roles: List[str] = field(default_factory=list)

    @property
    def is_admin(self) -> bool:
        return "system_admin" in self.roles or "tenant_admin" in self.roles


def _b64url(seg: str) -> bytes:
    seg += "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg.encode("ascii"))


def verify_hs256(token: str, secret: str, *, leeway: int = 0) -> Optional[dict]:
    """Decoded claims if ``token`` is a valid, unexpired HS256 JWT signed with
    ``secret``; otherwise None. The algorithm is pinned to HS256."""
    if not token or not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    h_seg, p_seg, s_seg = parts
    try:
        header = json.loads(_b64url(h_seg))
    except Exception:
        return None
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        return None
    expected = hmac.new(secret.encode("utf-8"), f"{h_seg}.{p_seg}".encode("ascii"), hashlib.sha256).digest()
    try:
        signature = _b64url(s_seg)
    except Exception:
        return None
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        claims = json.loads(_b64url(p_seg))
    except Exception:
        return None
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and time.time() > exp + leeway:
        return None
    return claims


def identity_from_claims(claims: dict, *, default_tenant: str = "default") -> Identity:
    roles = claims.get("roles") or []
    if isinstance(roles, str):
        roles = [roles]
    return Identity(
        user=claims.get("sub") or claims.get("user") or "",
        tenant=claims.get("tenant") or default_tenant,
        roles=list(roles),
    )


def make_secret_verifier(secret: str, *, default_tenant: str = "default"):
    """A verify_bearer(token) -> Identity|None backed by the shared HS256 secret."""
    def verify(token: str) -> Optional[Identity]:
        claims = verify_hs256(token, secret)
        return identity_from_claims(claims, default_tenant=default_tenant) if claims else None
    return verify


def make_service_cred_verifier(ldap_manager_url: str, internal_secret: str, *,
                               scope: str = "bcf", default_tenant: str = "default",
                               timeout: float = 3.0):
    """A verify_basic(key_id, secret, tenant, source_ip) -> Identity|None backed by a
    ``bcf``-scoped ``key:secret`` service credential (the gateway/non-interactive
    door). Delegates to ldap_manager's ``/internal/service-cred/verify`` — the SAME
    server-to-server API the WebDAV and MCP doors use — guarded by the shared
    ``SERVICE_CRED_INTERNAL_SECRET``. The credential carries no roles, so the caller
    acts as a plain member of its tenant (reads/writes still run through ACLs)."""
    url = ldap_manager_url.rstrip("/") + "/internal/service-cred/verify"

    def verify(key_id: str, secret: str, tenant: str, source_ip: Optional[str] = None) -> Optional[Identity]:
        if not key_id or not secret:
            return None
        payload = {
            "key_id": key_id, "secret": secret,
            "tenant": tenant or default_tenant, "scope": scope,
        }
        if source_ip:
            payload["source_ip"] = source_ip
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "X-Internal-Auth": internal_secret})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    return None
                body = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None  # bad credential (401), verifier down, or network error → deny
        uid = body.get("uid")
        if not uid:
            return None
        return Identity(user=uid, tenant=body.get("tenant") or tenant or default_tenant, roles=[])
    return verify


def current_identity(request: Request) -> Identity:
    """FastAPI dependency: the authenticated caller, or 401. Accepts EITHER a
    ``Bearer`` token (OAuth / the WebUI session JWT — ``app.state.verify_bearer``)
    or ``Basic key:secret`` (a ``bcf``-scoped gateway service credential —
    ``app.state.verify_basic``, when configured). Tests inject either verifier."""
    header = request.headers.get("authorization", "")
    scheme, _, rest = header.partition(" ")
    scheme = scheme.strip().lower()
    ident: Optional[Identity] = None

    if scheme == "bearer":
        verify = getattr(request.app.state, "verify_bearer", None)
        ident = verify(rest.strip()) if verify else None
    elif scheme == "basic":
        verify_basic = getattr(request.app.state, "verify_basic", None)
        if verify_basic:
            try:
                raw = base64.b64decode(rest.strip()).decode("utf-8")
                key_id, _, secret = raw.partition(":")
            except Exception:
                key_id = secret = ""
            source_ip = request.client.host if request.client else None
            ident = verify_basic(key_id, secret, request.headers.get("x-tenant", ""), source_ip)

    if ident is None or not ident.user:
        raise HTTPException(status_code=401, detail="authentication required")
    return ident
