# FileEngine CSAI — Convert/Search/AI service (FastAPI app + ingest worker).
#
# One image, two run modes (compose sets the command):
#   convert-search-ai          -> the HTTP app (search, RAG chat, doc text/preview)
#   convert-search-ai-worker   -> the event-driven ingest/rendition worker
#
# Installs the FULL conversion toolchain so previews/extraction never silently
# degrade (see IMPLEMENTATION_PLAN §3.1): system tools + Python PDF backends.
# docling (heavy ML) is gated by INSTALL_DOCLING (default on for production; the
# chain still works via pymupdf4llm/pdfplumber/pdftotext when off).
FROM fedora:43
ARG INSTALL_DOCLING=1

# System: Python + conversion tools.
#   LibreOffice (office->pdf), poppler-utils (pdftoppm/pdftotext), ImageMagick
#   (thumbnails), ffmpeg-free (video poster/clip; ships libopenh264), libmagic.
#   3D/CAD: opencascade-draw provides DRAWEXE (STEP/IGES/BREP/OBJ/VRML -> glTF);
#   nodejs/npm host convert2xkt (glTF/IFC/... -> XKT) for the xeokit viewer.
#   chromium (provides /usr/bin/chromium-browser) is the full-fidelity HTML->PDF
#   engine for .html documents incl. chat-generated reports (LibreOffice is the
#   fallback). pandoc renders chat "Generate report" output as an editable Word
#   (.docx) draft (CSAI_PANDOC_BIN).
RUN set -eux; \
    dnf -y install \
      python3 python3-pip \
      libreoffice-writer libreoffice-calc libreoffice-impress \
      pandoc \
      poppler-utils ImageMagick ffmpeg-free file-libs \
      opencascade-draw nodejs npm \
      chromium; \
    dnf clean all; rm -rf /var/cache/dnf

# --- Make OpenCASCADE's DRAWEXE actually usable ------------------------------
#
# opencascade-draw installs DRAWEXE, but two packaging gaps leave it inert, and
# BOTH fail silently: DRAWEXE exits 0 having done nothing, so the plugin's
# fail-soft path reports "no rendition" with no error anywhere. The symptom is
# STEP/IGES/BREP/OBJ/VRML files that upload and index fine but never get a 3D
# preview — while IFC works, because IFC goes through IfcOpenShell/convert2xkt
# and never touches DRAWEXE.
#
# 1. CASROOT is unset, and DRAWEXE looks for $CASROOT/src/DrawResources while
#    Fedora ships them under $CASROOT/resources/DrawResources. Without it:
#      couldn't read file ".../src/DrawResources/DrawDefault"
#
# 2. `pload` dlopen()s UNVERSIONED sonames (libTKDraw.so), but the runtime
#    package ships only libTKDraw.so.7.9 — the plain .so symlinks come from
#    opencascade-devel, which would drag in headers we do not want in a runtime
#    image. Without them every OCCT command is undefined:
#      invalid command name "ReadStep" / "incmesh" / "WriteGltf"
RUN set -eux; \
    ln -sfn /usr/share/opencascade/resources /usr/share/opencascade/src; \
    cd /usr/lib64; \
    for f in libTK*.so.*.*; do \
      base="${f%%.so.*}"; \
      [ -e "$base.so" ] || ln -sfn "$f" "$base.so"; \
    done; \
    ldconfig

ENV CASROOT=/usr/share/opencascade

# convert2xkt (xeokit-convert): the glTF/IFC/CityJSON/LAS/STL/PLY -> XKT hop, and
# the final hop for the OpenCASCADE CAD path. The npm bin is "xeokit-convert"; we
# also expose it under the conventional "convert2xkt" name (CSAI's default).
RUN set -eux; \
    npm install -g @xeokit/xeokit-convert@1.3.2; \
    script="$(npm root -g)/@xeokit/xeokit-convert/convert2xkt.js"; \
    chmod +x "$script"; \
    ln -sf "$script" /usr/local/bin/convert2xkt; \
    npm cache clean --force; rm -rf /root/.npm

# IfcOpenShell — full IFC (BIM) support: the IfcConvert binary (IFC -> OBJ/glTF/
# COLLADA, feeding convert2xkt) plus the ifcopenshell Python bindings for property/
# geometry extraction. Provided as a prebuilt RPM (rpms/ifcopenshell/), so IFC
# rendition/extraction is first-class rather than degrading to convert2xkt alone.
COPY rpms/ifcopenshell/*.rpm /tmp/ifc/
RUN set -eux; \
    dnf -y install /tmp/ifc/*.rpm; \
    dnf clean all; rm -rf /var/cache/dnf /tmp/ifc; \
    IfcConvert --version

# Python: the fileengine gRPC client, then CSAI with conversion + provider extras.
COPY build-src/python_interface /src/python_interface
COPY build-src/convert_search_ai /src/convert_search_ai
RUN set -eux; \
    pip3 install --no-cache-dir --break-system-packages /src/python_interface; \
    pip3 install --no-cache-dir --break-system-packages \
      "/src/convert_search_ai[pdf,pdf-pymupdf,anthropic,openai]"; \
    pip3 install --no-cache-dir --break-system-packages python-magic; \
    if [ "$INSTALL_DOCLING" = "1" ]; then \
      pip3 install --no-cache-dir --break-system-packages "docling>=2.0"; \
    fi

# Bind all interfaces in-container; the rest of the config comes from compose env.
ENV CSAI_HTTP_HOST=0.0.0.0 \
    CSAI_HTTP_PORT=8092
EXPOSE 8092
CMD ["convert-search-ai"]
