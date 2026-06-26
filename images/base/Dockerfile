# FileEngine base image — Fedora + the provided AWS SDK runtime libraries.
#
# Shared lower layer for the C++ service images (core, http-bridge,
# webdav-bridge). Those images `FROM fileengine-base` and `dnf install` their
# own FileEngine RPMs from rpms/fileengine/; the FileEngine binaries link the
# AWS SDK shared libs at runtime, and those libs are NOT in the Fedora repos, so
# they are installed here from the provided RPMs (rpms/aws-sdk/, v1.11.725).
#
# Fedora 43 matches the host the FileEngine RPMs are built on (….fc43.x86_64), so
# the C++ ABI and the .fcNN dist tag line up. Build context = docker_unified/.

FROM fedora:43

# The FileEngine binaries link only the S3 + core SDK and the CRT layer
# (libaws-cpp-sdk-s3 / -core, libaws-crt-cpp, and the aws-c-* C libs). These
# AWS SDK RPMs ship **unversioned** runtime .so files, and the `crt` package
# quirkily places its real runtime lib (libaws-crt-cpp.so) in `crt-devel` — so
# the runtime closure is: core, storage, crt, crt-extra, and crt-devel (for the
# crt-cpp wrapper). The other services (compute/database/management/…) and the
# remaining -devel headers are not linked and are intentionally omitted.
COPY rpms/aws-sdk/*.rpm /tmp/aws-sdk/
RUN set -eux; \
    dnf -y install \
        /tmp/aws-sdk/aws-sdk-cpp-core-1.11.725-*.rpm \
        /tmp/aws-sdk/aws-sdk-cpp-storage-1.11.725-*.rpm \
        /tmp/aws-sdk/aws-sdk-cpp-crt-1.11.725-*.rpm \
        /tmp/aws-sdk/aws-sdk-cpp-crt-extra-1.11.725-*.rpm \
        /tmp/aws-sdk/aws-sdk-cpp-crt-devel-1.11.725-*.rpm; \
    dnf clean all; \
    rm -rf /tmp/aws-sdk /var/cache/dnf
