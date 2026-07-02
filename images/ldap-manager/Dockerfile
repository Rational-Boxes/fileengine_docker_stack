# FileEngine LDAP Manager — tenant user/role admin API + self-service
# profile/password (FastAPI). Standalone: talks LDAP/Postgres/Redis/SMTP directly
# (no gRPC client, no conversion toolchain), so a lean Python base suffices.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY build-src/ldap_manager/pyproject.toml ./
COPY build-src/ldap_manager/src ./src
RUN pip install --no-cache-dir .

# Bind all interfaces in-container (reached only via the nginx /ldapadmin proxy);
# monitoring stays loopback-only per config. Runtime config comes from compose env.
ENV HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8093
EXPOSE 8093
CMD ["fileengine-ldap-manager"]
