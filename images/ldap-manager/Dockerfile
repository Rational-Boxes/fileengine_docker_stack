# FileEngine LDAP Manager — tenant user/role admin API + self-service
# profile/password (FastAPI). Standalone: talks LDAP/Postgres/Redis/SMTP directly
# (no gRPC client, no conversion toolchain), so a lean Python base suffices.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# audit_service first: it changes rarely, and ldap_manager imports its publisher
# to emit the auth-category events — sign-in, password set/reset, user create,
# role grant. It was missing from this image, and the failure was silent by
# design: AuditEmitter catches the ImportError, logs once at start-up, and then
# treats auditing as "off", which makes emit() return True so a fail-closed
# caller is never blocked by an audit system nobody configured.
#
# The cost of that combination is precise. public_auth and admin_users guard
# credential changes with `if not svc.audit.emit(...): raise 503` — a write-ahead
# that refuses the change unless it was recorded. With the package absent those
# guards passed unconditionally, so password resets and user creations went
# through unrecorded while every one of them looked audited.
COPY build-src/audit_service /app/audit_service
RUN pip install --no-cache-dir /app/audit_service

COPY build-src/ldap_manager/pyproject.toml ./
COPY build-src/ldap_manager/src ./src
RUN pip install --no-cache-dir .

# Assert the publisher actually imports. This is the one dependency whose absence
# this service is built to survive quietly, so a build-time check is the only
# place it can fail loudly.
RUN set -eux; \
    python -c "\
from audit_service.publisher import AuditPublisher; \
from ldap_manager.audit import AuditEmitter; \
e = AuditEmitter(True, publisher=AuditPublisher.from_env()); \
assert e.enabled, 'audit publisher did not initialise'; \
print('audit publisher OK')"

# Bind all interfaces in-container (reached only via the nginx /ldapadmin proxy);
# monitoring stays loopback-only per config. Runtime config comes from compose env.
ENV HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8093
EXPOSE 8093
CMD ["fileengine-ldap-manager"]
