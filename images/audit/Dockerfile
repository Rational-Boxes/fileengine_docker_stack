# FileEngine audit_service — the deployment-wide consumer that drains the
# security-event sink into per-tenant, hash-chained audit_log tables, plus the
# query/verify API the SPA's Security view is built on.
#
# ONE IMAGE, SEVERAL ROLES. The package ships five entry points and a deployment
# runs more than one container from this image, differing only by command:
#
#   audit-consumer  the WRITER. Drains fileengine:audit into audit_log. This is
#                   the one that must always run: every other service publishes
#                   to that Redis stream, and with no consumer the events are
#                   never written, the hash chain never advances, and the stream
#                   grows without bound because Redis streams do not self-trim.
#   audit-api       the READER, :8097. Serves /v1/audit/* and /v1/security/*.
#                   Without it the SPA's Security view has no backend.
#   audit-rules     the rules engine, evaluating windows into incidents.
#   audit-retention archival/pruning (needs the `retention` extra).
#   audit-verify    a CLI for re-walking the chain; not a long-running service.
#
# NO python_interface. Unlike share_service, this never calls the core over
# gRPC — it reads Redis and writes Postgres, and nothing else. Installing the
# gRPC client would add a dependency the audit path has no reason to carry.
#
# STORAGE. It uses the CORE database (fileengine); audit_log lives in the same
# per-tenant schemas as the rest of the tenant's data, so there is no separate
# database to provision.
FROM fedora:43

RUN set -eux; \
    dnf -y install python3 python3-pip; \
    dnf clean all; rm -rf /var/cache/dnf

WORKDIR /app

COPY build-src/audit_service/pyproject.toml build-src/audit_service/README.md /app/audit_service/
COPY build-src/audit_service/src/ /app/audit_service/src/

# [api] pulls FastAPI + uvicorn for audit-api; [retention] pulls cryptography +
# boto3 for the archival job. Both are installed so one image serves every role
# — the alternative is several near-identical images differing by one extra.
RUN set -eux; \
    pip3 install --no-cache-dir "/app/audit_service[api,retention]"

# Monitoring convention, as everywhere else: the API binds all interfaces inside
# the container (nginx fronts it), while unauthenticated endpoints stay on
# loopback WITHIN the container.
ENV AUDIT_API_HOST=0.0.0.0 \
    AUDIT_API_PORT=8097

EXPOSE 8097

# The writer is the default: an audit deployment that starts the API but not the
# consumer looks healthy while recording nothing.
CMD ["audit-consumer"]
