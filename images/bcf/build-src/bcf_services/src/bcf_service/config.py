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

"""Configuration for the BCF-API subservice, read from the environment.

A ``.env`` in the working directory is loaded automatically (without overriding
values already set in the environment), mirroring the discussion / CSAI / MCP
services. ``FILEENGINE_*`` names are shared with the core / bridges / mcp / CSAI /
discussion; service-specific knobs use the ``BCF_*`` prefix.
"""
import os


def load_dotenv(path: str = ".env") -> None:
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), _strip_value(val))


def _strip_value(val: str) -> str:
    """Parse a dotenv value: honor a surrounding quote, else drop an inline
    `` # …`` comment. A value that is *entirely* a comment yields ``""``."""
    val = val.strip()
    if val[:1] in ("'", '"'):
        q = val[0]
        end = val.find(q, 1)
        return val[1:end] if end != -1 else val[1:]
    if val.startswith("#"):
        return ""
    hi = val.find(" #")
    if hi != -1:
        val = val[:hi]
    return val.strip()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _first(*keys_and_default: str) -> str:
    *keys, default = keys_and_default
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return default


def _int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


# BCF versions this door can speak. 2.1 is the interop target (desktop BCF Managers
# round-trip it reliably); 3.0 is planned behind a serializer switch (§17 decision 4).
SUPPORTED_BCF_VERSIONS = ("2.1",)


class Config:
    def __init__(self) -> None:
        # --- gRPC core (shared with the bridges / mcp / CSAI / discussion) ---
        self.grpc_host = _env("FILEENGINE_GRPC_HOST", "localhost")
        self.grpc_port = _env("FILEENGINE_GRPC_PORT", "50051")
        self.grpc_address = f"{self.grpc_host}:{self.grpc_port}"

        # --- Postgres (the core's DB; per-tenant schemas hold the BCF tables) ---
        self.pg_host = _env("FILEENGINE_PG_HOST", "localhost")
        self.pg_port = _int("FILEENGINE_PG_PORT", 5434)
        self.pg_database = _env("FILEENGINE_PG_DATABASE", "fileengine")
        self.pg_user = _env("FILEENGINE_PG_USER", "postgres")
        self.pg_password = _env("FILEENGINE_PG_PASSWORD", "postgres")

        # --- Redis (shared broker — the same discussion events fan-out) ---
        self.redis_host = _env("FILEENGINE_REDIS_HOST", "localhost")
        self.redis_port = _int("FILEENGINE_REDIS_PORT", 6379)
        self.redis_password = _env("FILEENGINE_REDIS_PASSWORD", "")
        self.redis_db = _int("FILEENGINE_REDIS_DB", 0)

        # --- Auth: OAuth 2.0 / OIDC via ldap_manager (Phase 1.7) ---
        # The BCF-API /auth discovery advertises these to desktop tools; the service
        # verifies bridge-issued HS256 JWTs with the shared secret (JWKS wiring is a
        # Phase F follow-on). Long-lived sync sessions ⇒ refresh is supported there.
        self.jwt_secret = _env("FILEENGINE_JWT_SECRET", "")
        self.ldap_manager_url = _first("LDAP_MANAGER_URL", "FILEENGINE_LDAP_MANAGER_URL",
                                       "http://localhost:8093")
        # Gateway key:secret door (scope "bcf"): a non-interactive service credential
        # verified via ldap_manager's /internal/service-cred/verify, guarded by this
        # shared secret (falls back to MFA_INTERNAL_SECRET so ops manage one). When
        # unset, the Basic key:secret path is disabled and only Bearer auth works.
        self.service_cred_internal_secret = _first(
            "SERVICE_CRED_INTERNAL_SECRET", "MFA_INTERNAL_SECRET", "")
        self.service_cred_scope = "bcf"
        self.oauth_auth_url = _first(
            "BCF_OAUTH_AUTH_URL", "") or f"{self.ldap_manager_url}/oauth/authorize"
        self.oauth_token_url = _first(
            "BCF_OAUTH_TOKEN_URL", "") or f"{self.ldap_manager_url}/oauth/token"

        # --- This door's HTTP surface ---
        # Loopback by default (the edge TLS proxy fronts it in prod); the external
        # AEC tools reach it through that proxy, never the raw port.
        self.http_host = _env("BCF_HTTP_HOST", "127.0.0.1")
        self.http_port = _int("BCF_HTTP_PORT", 8098)

        # Default tenant when none is resolved from the request (dev convenience).
        self.tenant = _env("FILEENGINE_BCF_TENANT", "default")

        # CORS origins for a browser client (off unless set; never "*").
        self.cors_origins = [o.strip() for o in _env("BCF_CORS_ORIGINS", "").split(",") if o.strip()]
