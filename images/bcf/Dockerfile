# FileEngine BCF — the openBIM (BIM Collaboration Format) door.
#
# One image, one run mode:
#   bcf  -> the BCF-API + BCF-XML HTTP surface  :8098
#
# What it is, per the service's own README and XEOKIT_UPGRADE_MARKUP_BCF_PLAN §12:
# a protocol/format ADAPTER over the discussion store and the core file store, not
# a second issue database. It owns only the BCF projection tables, and those are a
# later phase — the BCF-XML import/export endpoints this image exists to serve are
# entirely stateless (topics arrive in the request body, a .bcfzip comes back), so
# the container needs no schema and touches no database at start-up.
#
# Pure-Python service with no conversion toolchain. It does not use the fileengine
# gRPC client — unlike discussion / csai / difference, nothing here imports it — so
# the build stages only its own source. psycopg/redis/grpcio come from its
# pyproject for the phases that will use them.
FROM fedora:43

RUN set -eux; dnf -y install python3 python3-pip; dnf clean all; rm -rf /var/cache/dnf

COPY build-src/bcf_services /src/bcf_services
RUN set -eux; pip3 install --no-cache-dir --break-system-packages /src/bcf_services

# Bind all interfaces IN-CONTAINER; the service defaults to 127.0.0.1, which would
# make it unreachable from nginx on the compose network. The loopback half of the
# monitoring convention (§9) is kept by the HOST publish — Ansible publishes this
# to 127.0.0.1 only — not by the in-container bind.
ENV BCF_HTTP_HOST=0.0.0.0 \
    BCF_HTTP_PORT=8098
EXPOSE 8098

# Fail the build if the app cannot be built or the export path is not mounted.
# Paths come from the OpenAPI schema, not app.routes: FastAPI keeps included
# routers as nested objects there, so walking app.routes finds only the four
# doc endpoints and an assertion against it passes or fails for the wrong reason.
RUN set -eux; \
    python3 -c "\
from bcf_service.app import build_app; from bcf_service.config import Config; \
from bcf_service.bcf_xml import export_bcfzip; \
paths = build_app(Config()).openapi()['paths']; \
want = '/bcf/{version}/bcf-xml/export'; \
assert want in paths, sorted(paths); \
assert 'post' in paths[want], paths[want]; \
assert export_bcfzip([], version='2.1')[:4] == b'PK\x03\x04', 'export did not produce a zip'; \
print('bcf routes OK:', len(paths), 'paths')"

CMD ["bcf-service"]
