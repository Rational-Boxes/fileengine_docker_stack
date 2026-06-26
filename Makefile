# Unified FileEngine stack — build pipeline (Phase 1)
#
# Produces the artifacts the compose images consume:
#   - FileEngine RPMs (core built with events ON, http-bridge, webdav-bridge),
#     built from each repo's committed HEAD via its own `make rpm-package`, then
#     staged into rpms/fileengine/.
#   - The built SPA (same-origin /api,/csai), staged into images/nginx/spa/.
#   - The shared base image (Fedora + provided AWS SDK runtime libs).
#
# The C++ service binaries come from prebuilt RPMs (per the design decision), not
# in-image source compilation. Run `make help` for targets.

SHELL := /bin/bash

# This Makefile lives in docker_unified/; the source repos are siblings.
ROOT         := $(abspath $(CURDIR)/..)
CORE_DIR     := $(ROOT)/file_engine_core
HTTP_DIR     := $(ROOT)/http_bridge
WEBDAV_DIR   := $(ROOT)/webdav_bridge
FRONTEND_DIR := $(ROOT)/frontend

VERSION  ?= 1.0.0
RPM_ROOT := $(HOME)/rpmbuild/RPMS

# Staging locations inside docker_unified/.
RPMS_DIR       := $(CURDIR)/rpms/fileengine
SPA_DIR        := $(CURDIR)/images/nginx/spa
MIGRATIONS_SRC := $(ROOT)/convert_search_ai/migrations
MIGRATIONS_DIR := $(CURDIR)/init/migrations

# Built into the SPA at compile time (apex the tenants live under). Empty here so
# a plain `make` works; set it for a real deployment: `make spa BASE_DOMAIN=host.com`.
BASE_DOMAIN ?=

# Image names.
BASE_IMAGE ?= fileengine-base:$(VERSION)

# Copy the newest RPM matching <pkg>-<version>-*.rpm from any RPMS arch dir into
# the staging dir. $(1) = package name. Fails loudly if the package is missing.
define stage_rpm
	f=$$(find $(RPM_ROOT) -name "$(1)-$(VERSION)-*.rpm" ! -name "*debuginfo*" ! -name "*debugsource*" \
	      -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-); \
	if [ -z "$$f" ]; then echo "  !! missing RPM: $(1)-$(VERSION)"; exit 1; fi; \
	cp -v "$$f" $(RPMS_DIR)/;
endef

.PHONY: help build rpms rpm-core rpm-http rpm-webdav spa stage-migrations stage-csai stage-mcp base-image clean

help:
	@echo "Unified FileEngine stack — Phase 1 build pipeline"
	@echo "  make build         Build + stage everything (rpms + spa)"
	@echo "  make rpms          Build the 3 FileEngine RPM sets, stage into rpms/fileengine/"
	@echo "  make rpm-core      Core RPMs only (built with events ON)"
	@echo "  make rpm-http      http-bridge RPM only"
	@echo "  make rpm-webdav    webdav-bridge RPM only"
	@echo "  make spa           Build the SPA, stage into images/nginx/spa/"
	@echo "                       (pass BASE_DOMAIN=host.com for subdomain tenancy)"
	@echo "  make base-image    Build the shared base image ($(BASE_IMAGE))"
	@echo "  make clean         Remove staged rpms/ + spa/ artifacts"

build: rpms spa stage-migrations stage-csai stage-mcp
	@echo "==> artifacts staged: rpms/ + spa/ + migrations/ + csai/build-src/ + mcp/build-src/"

# --- FileEngine RPMs -------------------------------------------------------

rpms: rpm-core rpm-http rpm-webdav
	@echo "==> staged RPMs:"
	@ls -1 $(RPMS_DIR)

rpm-core:
	@echo "==> building core RPMs (events ON) from $(CORE_DIR)"
	$(MAKE) -C $(CORE_DIR) rpm-package
	@mkdir -p $(RPMS_DIR)
	@set -e; $(call stage_rpm,fileengine-libs) $(call stage_rpm,fileengine-server) $(call stage_rpm,fileengine-cli)

rpm-http:
	@echo "==> building http-bridge RPM from $(HTTP_DIR)"
	$(MAKE) -C $(HTTP_DIR) rpm-package
	@mkdir -p $(RPMS_DIR)
	@set -e; $(call stage_rpm,fileengine-http-bridge)

rpm-webdav:
	@echo "==> building webdav-bridge RPM from $(WEBDAV_DIR)"
	$(MAKE) -C $(WEBDAV_DIR) rpm-package
	@mkdir -p $(RPMS_DIR)
	@set -e; $(call stage_rpm,fileengine-webdav-bridge)

# --- SPA -------------------------------------------------------------------

spa:
	@echo "==> building SPA (same-origin /api,/csai; BASE_DOMAIN='$(BASE_DOMAIN)')"
	cd $(FRONTEND_DIR) && npm ci && VITE_BASE_DOMAIN=$(BASE_DOMAIN) npm run build
	@mkdir -p $(SPA_DIR)
	@rm -rf $(SPA_DIR:%=%)/* && cp -r $(FRONTEND_DIR)/dist/. $(SPA_DIR)/
	@echo "==> SPA staged to $(SPA_DIR)"

# --- DB migrations (staged for db-init) ------------------------------------

# Stage the CSAI database-wide baseline (extensions) for the db-init service.
# db-init also inlines the extensions, so this is forward-compatible staging for
# any additional convert_search_ai migrations.
stage-migrations:
	@echo "==> staging CSAI migrations from $(MIGRATIONS_SRC)"
	@mkdir -p $(MIGRATIONS_DIR)
	@cp -v $(MIGRATIONS_SRC)/*.sql $(MIGRATIONS_DIR)/ 2>/dev/null || echo "  (no CSAI migrations found)"

# --- CSAI build source (staged for the fileengine-csai image) --------------

# The CSAI image needs the convert_search_ai service + the python_interface gRPC
# client (a sibling repo). Stage both into the image's build context.
stage-csai:
	@echo "==> staging CSAI + python_interface source into images/csai/build-src"
	@rm -rf images/csai/build-src
	@mkdir -p images/csai/build-src
	@cp -r $(ROOT)/convert_search_ai images/csai/build-src/convert_search_ai
	@cp -r $(ROOT)/python_interface images/csai/build-src/python_interface
	@find images/csai/build-src \( -name '.git' -o -name '__pycache__' -o -name '.venv' \
	    -o -name 'node_modules' -o -name '*.pyc' \) -prune -exec rm -rf {} + 2>/dev/null || true

# --- MCP build source (staged for the fileengine-mcp image) ----------------

# The MCP image needs the mcp server + the python_interface gRPC client.
stage-mcp:
	@echo "==> staging MCP + python_interface source into images/mcp/build-src"
	@rm -rf images/mcp/build-src
	@mkdir -p images/mcp/build-src
	@cp -r $(ROOT)/mcp images/mcp/build-src/mcp
	@cp -r $(ROOT)/python_interface images/mcp/build-src/python_interface
	@find images/mcp/build-src \( -name '.git' -o -name '__pycache__' -o -name '.venv' \
	    -o -name 'node_modules' -o -name '*.pyc' \) -prune -exec rm -rf {} + 2>/dev/null || true

# --- Base image ------------------------------------------------------------

base-image:
	@echo "==> building base image $(BASE_IMAGE)"
	docker build -t $(BASE_IMAGE) -f images/base/Dockerfile .

# --- Clean -----------------------------------------------------------------

clean:
	@echo "==> removing staged artifacts"
	@rm -f $(RPMS_DIR)/*.rpm
	@rm -rf $(SPA_DIR)
