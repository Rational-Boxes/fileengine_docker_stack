# FileEngine http-bridge — REST/auth proxy over the gRPC core (serves the SPA).
#
# Standalone binary: it links gRPC++/protobuf/Poco/OpenLDAP (pulled from the
# Fedora repos by the RPM's deps), NOT fileengine-libs, and uses no Postgres.
# Built on the shared base so the FileEngine images share one lower layer.
FROM fileengine-base:1.0.0

COPY rpms/fileengine/fileengine-http-bridge-*.rpm /tmp/rpms/
RUN set -eux; \
    dnf -y install /tmp/rpms/*.rpm; \
    dnf clean all; \
    rm -rf /tmp/rpms /var/cache/dnf

# Console logging for container capture; bind all interfaces; reach the core by
# its compose service name. The rest (LDAP, CORS, limits) comes from compose env.
ENV LOG_WRITE_TO_CONSOLE=true \
    HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8090 \
    HTTP_MONITORING_PORT=8091 \
    FILEENGINE_GRPC_HOST=core \
    FILEENGINE_GRPC_PORT=50051

# DynamicUser=yes in the systemd unit → no fixed RPM user; run unprivileged here.
USER nobody
EXPOSE 8090 8091
ENTRYPOINT ["/usr/bin/fileengine-http-bridge"]
