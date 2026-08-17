# FileEngine folder_actions — event-driven actions bound to a virtual folder.
#
# One image, three run modes (compose sets the command), mirroring discussion:
#   folder-actions            -> the admin API (bindings, classifier editor)   :8099
#   folder-actions-consumer   -> the recognized-event worker (fileengine:events)
#   folder-actions-reconcile  -> the periodic reconcile sweep (outage catch-up)
#
# Pure-Python service (no conversion toolchain): reuses the fileengine gRPC client
# for permission checks exactly as CSAI / discussion / the MCP server do. The
# upstream repo ships its own Containerfile for standalone podman builds; this is
# the stack variant — same Fedora base as the rest of the images, sources staged
# into build-src by `make stage-folder-actions`.
FROM fedora:43

RUN set -eux; dnf -y install python3 python3-pip; dnf clean all; rm -rf /var/cache/dnf

# The fileengine gRPC client first (changes rarely -> better layer caching), then
# the folder_actions service.
COPY build-src/python_interface /src/python_interface
COPY build-src/folder_actions /src/folder_actions
RUN set -eux; \
    pip3 install --no-cache-dir --break-system-packages /src/python_interface; \
    pip3 install --no-cache-dir --break-system-packages /src/folder_actions

# Bind all interfaces in-container; the rest of the config comes from compose env.
# The host still fronts loopback per the monitoring-port convention (§9).
ENV FA_HTTP_HOST=0.0.0.0 \
    FA_HTTP_PORT=8099
EXPOSE 8099
CMD ["folder-actions"]
