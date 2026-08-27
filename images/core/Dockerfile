# FileEngine core — gRPC service image.
#
# Layers the prebuilt FileEngine RPMs (built with Redis events ON) onto the
# shared base (Fedora + AWS SDK runtime libs). dnf resolves the runtime deps
# (postgresql-libs, openssl, libcurl, libuuid, hiredis) from the Fedora repos;
# the fileengine-libs RPM Requires hiredis, so the events path is wired.
FROM fileengine-base:1.2.0

# The CLI is installed alongside the server, not because the container runs it
# as a command, but because credential administration happens INSIDE this
# container: service-auth-init issues tokens via `podman/docker exec ...
# fileengine_cli`, and the Ansible service_auth role does the same on the host.
# Issuing is a database write hashed under the pepper, and the pepper is already
# in this container's environment — so doing it here means the pepper does not
# have to exist in a second place that can drift from the first.
COPY rpms/fileengine/fileengine-libs-*.rpm \
     rpms/fileengine/fileengine-server-*.rpm \
     rpms/fileengine/fileengine-cli-*.rpm \
     /tmp/rpms/
RUN set -eux; \
    dnf -y install /tmp/rpms/*.rpm; \
    dnf clean all; \
    rm -rf /tmp/rpms /var/cache/dnf

# Logs to stdout for container log capture; config comes from the environment
# (FILEENGINE_* — config source #4) injected by compose.
ENV FILEENGINE_LOG_TO_CONSOLE=true \
    FILEENGINE_LOG_TO_FILE=false \
    FILEENGINE_GRPC_HOST=0.0.0.0 \
    FILEENGINE_GRPC_PORT=50051 \
    FILEENGINE_HTTP_METRICS_ADDR=0.0.0.0 \
    FILEENGINE_HTTP_METRICS_PORT=8081

USER fileengine
EXPOSE 50051 8081
ENTRYPOINT ["/usr/bin/fileengine_server"]
