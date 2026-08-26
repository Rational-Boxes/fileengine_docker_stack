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
#   *-fonts           glyph outlines for the PDF vector tier — see below
FROM fedora:43

# FONTS ARE NOT COSMETIC HERE. The vector tier outlines every string into <path>
# geometry so a diff renders identically everywhere ("no client fonts"), and it
# can only do that if it has a font file to read outlines *from*. The Fedora base
# ships neither Liberation nor DejaVu, so without this the glyph provider finds
# nothing, plugins/pdf.py::_tier1_possible() refuses tier 1 for every page, and
# every PDF comparison silently comes out as a raster diff that looks finished.
#
# The set matches plugins/pdf_glyphs.py::_FACES, and the metric-compatible ones
# earn their place: same advance widths as the proprietary faces they stand in
# for, so a substituted run is the same length as the author's and the diff shows
# what changed in the document rather than what changed in the rendering.
#   liberation-*      Arial / Times New Roman / Courier New
#   google-carlito    Calibri
#   crosextra-caladea Cambria
#   urw-base35        the base-35 PostScript faces (Palatino, Bookman, Century
#                     Schoolbook, Avant Garde, Chancery, Symbol, Dingbats, Nimbus)
#   dejavu / open-sans nearest open face for Verdana, Segoe UI, Consolas, ...
RUN set -eux; \
    dnf -y install \
      python3 python3-pip \
      poppler-utils \
      opencascade-draw nodejs npm \
      liberation-sans-fonts liberation-serif-fonts liberation-mono-fonts \
      google-carlito-fonts google-crosextra-caladea-fonts \
      urw-base35-fonts dejavu-fonts-all open-sans-fonts; \
    dnf clean all; rm -rf /var/cache/dnf

# convert2xkt, pinned to the same version CSAI uses so a diff rendition and the
# rendition it is compared against come out of the same converter.
RUN set -eux; \
    npm install -g @xeokit/xeokit-convert@1.3.2; \
    script="$(npm root -g)/@xeokit/xeokit-convert/convert2xkt.js"; \
    chmod +x "$script"; \
    ln -sf "$script" /usr/local/bin/convert2xkt; \
    npm cache clean --force; rm -rf /root/.npm

# IfcOpenShell. The comment that used to sit here said IFC "still compares, by
# geometry, one tier down" without it. THAT IS NOT TRUE and it cost a production
# outage: plugins/three_d.py::_load() routes ifc to parse_ifc() and nowhere else,
# parse_ifc() returns an empty model tagged "ifcopenshell-missing", and diff()
# ends at DiffResult.failed("parse", ...). There is no geometry fallback for IFC
# because nothing else in the image can read it — DRAWEXE has no IFC reader. So
# for IFC this dependency is REQUIRED, not optional, and its absence is a hard
# failure of every .ifc comparison rather than a quieter one.
#
# INSTALL_IFC is therefore "auto" and not "0": if RPMs are staged we install
# them. A clean checkout with an empty ifc-rpms/ still builds (that is why the
# flag existed at all) but now says plainly what the resulting image cannot do.
#   INSTALL_IFC=1  require them — fail the build if they are not staged
#   INSTALL_IFC=0  deliberately build an image that cannot compare IFC
#
# The build verifies the *Python bindings* import, not just that IfcConvert runs:
# the RPM installs them under a hard-coded python3.N site-packages path, so a
# base-image Python bump would silently revert us to exactly the failure this
# block exists to fix, with a binary that still answers --version.
ARG INSTALL_IFC=auto
COPY build-src/ifc-rpms/ /tmp/ifc/
RUN set -eux; \
    if [ "$INSTALL_IFC" = "0" ]; then \
      echo "!! IfcOpenShell skipped (INSTALL_IFC=0): every .ifc comparison will FAIL"; \
    elif ls /tmp/ifc/*.rpm >/dev/null 2>&1; then \
      dnf -y install /tmp/ifc/*.rpm; \
      dnf clean all; rm -rf /var/cache/dnf; \
      IfcConvert --version; \
      python3 -c "import ifcopenshell, ifcopenshell.geom; print('ifcopenshell OK', ifcopenshell.version)"; \
    elif [ "$INSTALL_IFC" = "1" ]; then \
      echo "!! INSTALL_IFC=1 but no RPMs in build-src/ifc-rpms/ (run: make stage-ifc IFC_RPM_DIR=...)"; \
      exit 1; \
    else \
      echo "!! no IfcOpenShell RPMs staged: every .ifc comparison will FAIL (not degrade)"; \
      echo "!! stage them with: make stage-ifc IFC_RPM_DIR=<IfcOpenShell>/build-fedora/assets"; \
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

# Assert the PDF vector tier can actually run, because its failure mode is
# silence: with no fontTools or no font file the glyph provider just reports
# "unavailable", _tier1_possible() declines, and every comparison completes as a
# raster diff with a ready manifest. Nothing errors and nothing looks wrong. That
# shipped once; a build-time check is what stops it shipping again.
RUN set -eux; \
    python3 -c "\
import sys; from fontTools.ttLib import TTFont; \
from difference_service.plugins.pdf_glyphs import GlyphProvider; \
g = GlyphProvider(); \
missing = [f for f in ('Arial','Times New Roman','Courier New','Calibri','Cambria','Palatino','Symbol') if not g.available(f)]; \
sys.exit('!! no outline source for: %s' % missing) if missing else print('pdf vector tier OK')"

# Bind all interfaces in-container; the rest of the config comes from compose env.
# The host still fronts loopback per the monitoring-port convention (§9).
ENV DIFF_HTTP_HOST=0.0.0.0 \
    DIFF_HTTP_PORT=8100
EXPOSE 8100
CMD ["difference-service"]
