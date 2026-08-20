# FileEngine share_service — time- and use-bounded links for people OUTSIDE the
# tenant (OUTSIDE_SHARE_LINKS §4).
#
# Two ports, and the split is the point:
#   share       -> the API, and the only unauthenticated door in the platform  :8101
#   monitoring  -> /healthz /readyz /poolz /metrics, LOOPBACK ONLY             :8102
#
# The upstream repo ships its own Containerfile (python:3.12-slim, built with
# the monorepo as context) for standalone podman builds. This is the stack
# variant: same Fedora base as the rest of the images, sources staged into
# build-src by `make stage-share`.
#
# TOOLCHAIN. Deliberately bare — no converters, no geometry tools, no browser.
# This service moves bytes it never inspects: a folder download is a store-only
# zip64 assembled in Python as the members stream through, and a drop is handed
# to the core chunk by chunk. Anything heavier would be a dependency on a path
# reachable without an account, which is the one place that cost is least
# acceptable.
FROM fedora:43

RUN set -eux; \
    dnf -y install python3 python3-pip; \
    dnf clean all; rm -rf /var/cache/dnf

WORKDIR /app

# Reused packages FIRST (they change rarely -> better layer caching).
#
# python_interface is the gRPC client. Every core call is delegated through it
# AS THE LINK CREATOR (§4.1) — there is no service principal, deliberately: an
# agent identity would outlive any creator's access, which is the lingering
# grant the authority re-check exists to prevent.
#
# audit_service is NOT optional here, unlike in services that import it
# opportunistically. The core attributes delegated activity to the creator, so
# this chain is the only record anywhere that an access was EXTERNAL (§4.3).
# With no publisher the service refuses to mint or redeem and /readyz stays red,
# so installing it is what makes the image functional at all. (ldap_manager's
# image does not install it and simply runs unaudited — correct for its
# emitter's semantics, and a dead container here.)
COPY build-src/python_interface/ /app/python_interface/
COPY build-src/audit_service/ /app/audit_service/
COPY build-src/share_service/pyproject.toml build-src/share_service/README.md /app/share_service/
COPY build-src/share_service/src/ /app/share_service/src/

RUN set -eux; \
    pip3 install --no-cache-dir /app/python_interface; \
    pip3 install --no-cache-dir /app/audit_service; \
    pip3 install --no-cache-dir /app/share_service

# Bind all interfaces INSIDE the container for the API; nginx still fronts it.
# Monitoring stays on loopback WITHIN the container, per the platform convention
# for unauthenticated endpoints — a scraper reaches it by exec or sidecar, never
# across the network.
#
# SHARE_ENABLED is NOT set here. It defaults to false in config.py and compose
# is where a deployment opts in: enabling this creates the only surface in the
# platform reachable without an account, and that should be a decision someone
# makes, never something they upgrade into.
ENV SHARE_API_HOST=0.0.0.0 \
    SHARE_API_PORT=8101 \
    SHARE_MONITORING_HOST=127.0.0.1 \
    SHARE_MONITORING_PORT=8102

EXPOSE 8101

CMD ["share-service"]
