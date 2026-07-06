# FileEngine Discussion — document-anchored threaded comments & review.
#
# One image, four run modes (compose sets the command), mirroring CSAI:
#   discussion         -> the HTTP API (threads/comments, dashboard feeds, live WS)  :8094
#   discuss-consumer   -> the core-event Redis consumer (activity projection, ACL cache)
#   discuss-mcp-http   -> the MCP door (Streamable-HTTP) for agents                  :8095
#   discuss-digest     -> ONE hourly digest pass (invoked by the digest-cron loop)
#
# Pure-Python service (no conversion toolchain): reuses the fileengine gRPC client
# for permission checks exactly as CSAI / the MCP server do.
FROM fedora:43

RUN set -eux; dnf -y install python3 python3-pip; dnf clean all; rm -rf /var/cache/dnf

# The fileengine gRPC client, then the discussion service with the MCP extra.
COPY build-src/python_interface /src/python_interface
COPY build-src/discussion_threaded_communication /src/discussion
RUN set -eux; \
    pip3 install --no-cache-dir --break-system-packages /src/python_interface; \
    pip3 install --no-cache-dir --break-system-packages "/src/discussion[mcp]"

# Hourly digest scheduler (used by the discussion-digest service).
COPY digest-cron.sh /usr/local/bin/digest-cron
RUN chmod +x /usr/local/bin/digest-cron

# Bind all interfaces in-container; the rest of the config comes from compose env.
# The MCP door binds DISC_HTTP_PORT + 1 (8094 -> 8095).
ENV DISC_HTTP_HOST=0.0.0.0 \
    DISC_HTTP_PORT=8094
EXPOSE 8094 8095
CMD ["discussion"]
