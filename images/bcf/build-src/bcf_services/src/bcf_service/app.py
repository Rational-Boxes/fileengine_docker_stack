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

"""FastAPI application factory for the BCF-API subservice.

``build_app`` stays pure (no .env side effects) so tests are hermetic; ``create_app``
loads ``./.env`` first for real launches. The HTTP surface is two routers:
``monitoring`` (loopback health) and ``bcf_api`` (the BCF-API discovery surface;
data endpoints land in Phase F).
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from typing import Callable, Optional

from . import __version__
from .auth import make_secret_verifier, make_service_cred_verifier
from .bcf_api import router as bcf_router
from .bcf_data_api import router as bcf_data_router
from .bcf_xml_api import router as bcf_xml_router
from .config import Config
from .monitoring import MONITORING_PATHS, router as monitoring_router
from .stores import BcfStore

log = logging.getLogger("bcf_service.app")


def build_app(
    config: Config | None = None,
    *,
    store: Optional[BcfStore] = None,
    verify_bearer: Optional[Callable] = None,
    verify_basic: Optional[Callable] = None,
) -> FastAPI:
    config = config or Config()
    app = FastAPI(title="bcf_service", version=__version__)
    app.state.config = config
    # Issue store (in-memory dev/test impl; discussion-backed comment_store + BCF
    # projection tables in production, §13) and the bearer verifier (config HS256
    # secret by default; tests inject an identity).
    app.state.store = store or BcfStore()
    app.state.verify_bearer = verify_bearer or make_secret_verifier(
        config.jwt_secret, default_tenant=config.tenant)
    # Gateway key:secret door (scope "bcf"): enabled only when the internal secret is
    # configured. Tests may inject a fake verify_basic directly. When neither is set,
    # Basic auth is unavailable and only Bearer (OAuth / WebUI session) is accepted.
    if verify_basic is not None:
        app.state.verify_basic = verify_basic
    elif config.service_cred_internal_secret and config.ldap_manager_url:
        app.state.verify_basic = make_service_cred_verifier(
            config.ldap_manager_url, config.service_cred_internal_secret,
            scope=config.service_cred_scope, default_tenant=config.tenant)
    else:
        app.state.verify_basic = None

    # Route-scoped IP allowlist for the unauthenticated monitoring endpoints. They
    # already bind loopback; when FILEENGINE_MONITORING_ALLOW_IPS is set
    # (comma-separated client IPs), a monitoring request from a non-listed address
    # is refused with 403 — matching the discussion / bridge convention.
    monitor_allow = {ip.strip() for ip in
                     os.environ.get("FILEENGINE_MONITORING_ALLOW_IPS", "").split(",") if ip.strip()}

    @app.middleware("http")
    async def _guard_monitoring(request, call_next):
        if monitor_allow and request.url.path in MONITORING_PATHS:
            client = request.client.host if request.client else ""
            if client not in monitor_allow:
                return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)

    # Browser CORS for a SPA on another origin (off unless configured; never "*").
    if config.cors_origins:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(monitoring_router)
    app.include_router(bcf_router)
    app.include_router(bcf_data_router)
    app.include_router(bcf_xml_router)
    # Prometheus scrape endpoint, guarded by the same allowlist as the other
    # monitoring routes. Reports process and per-thread state so a stuck or
    # leaking service is visible to the same scraper that watches the core.
    from . import metrics as _fe_metrics
    _fe_metrics.install(app, "bcf_service", [], {"version": str(__version__)})

    return app


def create_app() -> FastAPI:
    """ASGI factory that loads ``./.env`` then builds the app — for launching via
    ``uvicorn bcf_service.app:create_app --factory`` or the ``bcf-service`` script."""
    from .config import load_dotenv
    load_dotenv()
    return build_app(Config())


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    app = create_app()
    cfg = app.state.config
    log.info("bcf_service %s — http=%s:%s core=%s", __version__, cfg.http_host, cfg.http_port,
             cfg.grpc_address)
    uvicorn.run(app, host=cfg.http_host, port=cfg.http_port)


if __name__ == "__main__":
    main()
