# FileEngine MCP server — Streamable-HTTP transport (for AI agents).
#
# Reuses the FileEngine Python client (python_interface). Both repos are staged
# into build-src/ by `make stage-mcp`. Per-request auth against LDAP; reaches the
# core over gRPC. Tenant resolved from X-Tenant or the Host subdomain.
FROM python:3.12-slim

WORKDIR /app
COPY build-src/python_interface/ /app/python_interface/
COPY build-src/mcp/pyproject.toml build-src/mcp/README.md /app/mcp/
COPY build-src/mcp/src/ /app/mcp/src/

RUN pip install --no-cache-dir /app/python_interface && \
    pip install --no-cache-dir /app/mcp

ENV MCP_HTTP_HOST=0.0.0.0 \
    MCP_HTTP_PORT=8089
EXPOSE 8089
CMD ["fileengine-mcp-http"]
