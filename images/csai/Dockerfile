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
RUN set -eux; \
    dnf -y install \
      python3 python3-pip \
      libreoffice-writer libreoffice-calc libreoffice-impress \
      poppler-utils ImageMagick ffmpeg-free file-libs; \
    dnf clean all; rm -rf /var/cache/dnf

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
