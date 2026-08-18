# FileEngine difference_service — version-to-version comparison renditions.
#
# One image, three run modes (compose sets the command), mirroring discussion and
# folder-actions:
#   difference             -> the HTTP API (diff manifests, rendition serving)  :8100
#   difference-consumer    -> the event worker (fileengine:events)
#   difference-reconcile   -> the periodic backfill sweep
#
# The upstream repo ships its own Containerfile (python:3.12-slim, built with the
# monorepo as context) for standalone podman builds. This is the stack variant:
# same Fedora base as the rest of the images, sources staged into build-src by
# `make stage-difference`.
#
# TOOLCHAIN. The comparison plugins shell out to external tools and are written to
# degrade a tier when one is absent rather than fail the file (SPECIFICATION §4),
# so this installs the geometry/PDF toolchain but nothing beyond it — no
# LibreOffice, ImageMagick, ffmpeg or chromium. Those belong to CSAI, which does
# document conversion; this service only compares what already converted.
#   poppler-utils     pdftoppm — the raster fallback tier for PDF pages
#   opencascade-draw  DRAWEXE  — STEP/IGES/BREP tessellation for CAD comparison
#   nodejs/npm        host convert2xkt (glTF -> XKT) for the xeokit viewer
FROM fedora:43

RUN set -eux; \
    dnf -y install \
      python3 python3-pip \
      poppler-utils \
      opencascade-draw nodejs npm; \
    dnf clean all; rm -rf /var/cache/dnf

# convert2xkt, pinned to the same version CSAI uses so a diff rendition and the
# rendition it is compared against come out of the same converter.
RUN set -eux; \
    npm install -g @xeokit/xeokit-convert@1.3.2; \
    script="$(npm root -g)/@xeokit/xeokit-convert/convert2xkt.js"; \
    chmod +x "$script"; \
    ln -sf "$script" /usr/local/bin/convert2xkt; \
    npm cache clean --force; rm -rf /root/.npm

# IfcOpenShell — OPTIONAL here, unlike in the CSAI image. It enables the IFC
# GlobalId object matcher (the highest-fidelity 3D tier); without it IFC files
# still compare, by geometry, one tier down. It is off by default because the
# RPMs are supplied out-of-band into rpms/ifcopenshell/ and are not in this repo,
# so requiring them would make a clean `make build` fail. Turn it on with:
#   docker build --build-arg INSTALL_IFC=1 ...
# after staging the RPMs (`make stage-ifc IFC_RPM_DIR=...`).
ARG INSTALL_IFC=0
COPY build-src/ifc-rpms/ /tmp/ifc/
RUN set -eux; \
    if [ "$INSTALL_IFC" = "1" ]; then \
      dnf -y install /tmp/ifc/*.rpm; \
      dnf clean all; rm -rf /var/cache/dnf; \
      IfcConvert --version; \
    else \
      echo "IfcOpenShell not installed (INSTALL_IFC=0): IFC compares by geometry, not GlobalId"; \
    fi; \
    rm -rf /tmp/ifc

# Python: the fileengine gRPC client first (changes rarely -> better layer
# caching), then the service with the PDF and raster extras. The `ifc` extra
# (ifcopenshell) is deliberately NOT taken from pip — it comes from the RPM above
# when enabled, matching how CSAI provisions it.
COPY build-src/python_interface /src/python_interface
COPY build-src/difference_service /src/difference_service
RUN set -eux; \
    pip3 install --no-cache-dir --break-system-packages /src/python_interface; \
    pip3 install --no-cache-dir --break-system-packages "/src/difference_service[pdf,raster]"

# Bind all interfaces in-container; the rest of the config comes from compose env.
# The host still fronts loopback per the monitoring-port convention (§9).
ENV DIFF_HTTP_HOST=0.0.0.0 \
    DIFF_HTTP_PORT=8100
EXPOSE 8100
CMD ["difference-service"]
