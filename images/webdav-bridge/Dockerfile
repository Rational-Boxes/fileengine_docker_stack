# FileEngine webdav-bridge — WebDAV gateway over the gRPC core.
#
# Standalone binary (gRPC++/protobuf/Poco/OpenLDAP from the Fedora repos; no
# fileengine-libs, no Postgres). Tenant is resolved from the request Host
# (<tenant>-drive.<base>); auth is per-request against LDAP.
FROM fileengine-base:1.2.0

COPY rpms/fileengine/fileengine-webdav-bridge-*.rpm /tmp/rpms/
RUN set -eux; \
    dnf -y install /tmp/rpms/*.rpm; \
    dnf clean all; \
    rm -rf /tmp/rpms /var/cache/dnf

ENV LOG_WRITE_TO_CONSOLE=true \
    WEBDAV_HOST=0.0.0.0 \
    WEBDAV_PORT=8088 \
    WEBDAV_MONITORING_PORT=8089 \
    FILEENGINE_GRPC_HOST=core \
    FILEENGINE_GRPC_PORT=50051

USER nobody
EXPOSE 8088 8089
ENTRYPOINT ["/usr/bin/fileengine-webdav-bridge"]
