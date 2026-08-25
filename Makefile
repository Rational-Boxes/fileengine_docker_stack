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

# Stack release version — tags the built images (fileengine-*:$(VERSION)) and the
# shared base image. Independent of the component RPM versions below.
VERSION  ?= 1.7.1

# Per-component RPM versions. The source repos version independently (core moved
# to 2.x; the bridges are on 1.x), so each is selected separately when staging.
# Override on the command line if you build a different point release.
CORE_VERSION   ?= 2.1.0
HTTP_VERSION   ?= 1.1.0
WEBDAV_VERSION ?= 1.1.0

RPM_ROOT := $(HOME)/rpmbuild/RPMS

# Staging locations inside docker_unified/.
RPMS_DIR       := $(CURDIR)/rpms/fileengine
SPA_DIR        := $(CURDIR)/images/nginx/spa
MIGRATIONS_SRC := $(ROOT)/convert_search_ai/migrations
MIGRATIONS_DIR := $(CURDIR)/init/migrations

# Cruft pruned from staged source trees before they become an image build context:
# VCS metadata, Python caches/virtualenvs, JS deps, and editor/IDE files (.idea,
# .vscode, *.iml, swap/backup files, .DS_Store). Keeps images lean + reproducible.
#
# SECURITY: also prune local runtime secrets and logs so they never land in an
# image layer — real `.env` files (NOT the `.env.example` / `.env-default`
# templates, which hold only placeholders) and any `*.log` / `*.audit` output.
# Runtime config is injected via compose env at deploy time, never baked in.
STAGE_PRUNE := \( -name '.git' -o -name '__pycache__' -o -name '.venv' \
	-o -name 'node_modules' -o -name '*.pyc' -o -name '.idea' -o -name '.vscode' \
	-o -name '*.iml' -o -name '.DS_Store' -o -name '*~' -o -name '*.swp' -o -name '*.swo' \
	-o -name '.env' -o -name '.env.local' -o -name '*.log' -o -name '*.audit' \)

# Vestigial: passed to the SPA build as VITE_BASE_DOMAIN, which nothing in the
# SPA reads any more. Tenancy comes from the request host and the sign-in label
# from the bridge, both at run time — the same reason the OAuth provider list
# stopped being a build-time variable: one image, many deployments. Kept so an
# existing `make spa BASE_DOMAIN=…` invocation does not break; setting it has no
# effect on the output.
BASE_DOMAIN ?=

# Image names.
BASE_IMAGE ?= fileengine-base:$(VERSION)

# Copy the newest RPM matching <pkg>-<version>-*.rpm from any RPMS arch dir into
# the staging dir. $(1) = package name, $(2) = version. Fails loudly if missing.
define stage_rpm
	f=$$(find $(RPM_ROOT) -name "$(1)-$(2)-*.rpm" ! -name "*debuginfo*" ! -name "*debugsource*" \
	      -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-); \
	if [ -z "$$f" ]; then echo "  !! missing RPM: $(1)-$(2)"; exit 1; fi; \
	cp -v "$$f" $(RPMS_DIR)/;
endef

.PHONY: help build rpms rpm-core rpm-http rpm-webdav spa stage-migrations stage-csai stage-mcp stage-ldap-manager stage-discussion stage-folder-actions stage-difference stage-share stage-ifc base-image publish clean

# Image set (fileengine-<name>:$(VERSION)); used by `publish`.
IMAGES := base core http-bridge webdav-bridge csai mcp ldap-manager discussion folder-actions difference share audit nginx ldap

help:
	@echo "Unified FileEngine stack — Phase 1 build pipeline"
	@echo "  make build         Build + stage everything (rpms + spa)"
	@echo "  make rpms          Build the 3 FileEngine RPM sets, stage into rpms/fileengine/"
	@echo "  make rpm-core      Core RPMs only (built with events ON)"
	@echo "  make rpm-http      http-bridge RPM only"
	@echo "  make rpm-webdav    webdav-bridge RPM only"
	@echo "  make spa           Build the SPA, stage into images/nginx/spa/"
	@echo "                       (no domain needed — tenancy is resolved at run time)"
	@echo "  make stage-discussion  Stage the discussion (comments) service source"
	@echo "  make stage-folder-actions  Stage the folder_actions service source"
	@echo "  make stage-difference  Stage the difference_service source"
	@echo "  make stage-share       Stage the share_service source"
	@echo "  make stage-ifc IFC_RPM_DIR=<dir>   Stage IfcOpenShell RPMs (REQUIRED by csai)"
	@echo "  make base-image    Build the shared base image ($(BASE_IMAGE))"
	@echo "  make publish REGISTRY=<host/ns>   Tag + push all fileengine-*:$(VERSION) to a registry"
	@echo "  make clean         Remove staged rpms/ + spa/ artifacts"

build: rpms spa stage-migrations stage-csai stage-mcp stage-ldap-manager stage-discussion stage-folder-actions stage-difference stage-share stage-audit
	@echo "==> artifacts staged: rpms/ + spa/ + migrations/ + csai/ + mcp/ + ldap-manager/ + discussion/ + folder-actions/ + difference/ + share/ + audit/ build-src"

# --- FileEngine RPMs -------------------------------------------------------

rpms: rpm-core rpm-http rpm-webdav
	@echo "==> staged RPMs:"
	@ls -1 $(RPMS_DIR)

rpm-core:
	@echo "==> building core RPMs (events ON) from $(CORE_DIR)"
	$(MAKE) -C $(CORE_DIR) rpm-package
	@mkdir -p $(RPMS_DIR)
	@set -e; $(call stage_rpm,fileengine-libs,$(CORE_VERSION)) $(call stage_rpm,fileengine-server,$(CORE_VERSION)) $(call stage_rpm,fileengine-cli,$(CORE_VERSION))

rpm-http:
	@echo "==> building http-bridge RPM from $(HTTP_DIR)"
	$(MAKE) -C $(HTTP_DIR) rpm-package
	@mkdir -p $(RPMS_DIR)
	@set -e; $(call stage_rpm,fileengine-http-bridge,$(HTTP_VERSION))

rpm-webdav:
	@echo "==> building webdav-bridge RPM from $(WEBDAV_DIR)"
	$(MAKE) -C $(WEBDAV_DIR) rpm-package
	@mkdir -p $(RPMS_DIR)
	@set -e; $(call stage_rpm,fileengine-webdav-bridge,$(WEBDAV_VERSION))

# --- SPA -------------------------------------------------------------------

# BASE_DOMAIN is passed through for compatibility but the SPA no longer reads it:
# tenancy comes from the request host and the sign-in label from the bridge, both
# at run time, so one build serves any domain. Baking a domain in was what made
# the packaged image wrong for every deployment but the one it was built for.
spa:
	@echo "==> building SPA (same-origin /api,/csai; tenancy resolved at run time)"
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
	@find images/csai/build-src $(STAGE_PRUNE) -prune -exec rm -rf {} + 2>/dev/null || true

# The ldap-manager image needs just the standalone FastAPI service (no gRPC
# client — it talks LDAP/Postgres/Redis/SMTP directly).
stage-ldap-manager:
	@echo "==> staging ldap_manager source into images/ldap-manager/build-src"
	@rm -rf images/ldap-manager/build-src
	@mkdir -p images/ldap-manager/build-src
	@cp -r $(ROOT)/ldap_manager images/ldap-manager/build-src/ldap_manager
	@find images/ldap-manager/build-src $(STAGE_PRUNE) -prune -exec rm -rf {} + 2>/dev/null || true

# --- MCP build source (staged for the fileengine-mcp image) ----------------

# The MCP image needs the mcp server + the python_interface gRPC client.
stage-mcp:
	@echo "==> staging MCP + python_interface source into images/mcp/build-src"
	@rm -rf images/mcp/build-src
	@mkdir -p images/mcp/build-src
	@cp -r $(ROOT)/mcp images/mcp/build-src/mcp
	@cp -r $(ROOT)/python_interface images/mcp/build-src/python_interface
	@find images/mcp/build-src $(STAGE_PRUNE) -prune -exec rm -rf {} + 2>/dev/null || true

# --- Discussion build source (staged for the fileengine-discussion image) ---

# The discussion (threaded comments) image needs the discussion service + the
# python_interface gRPC client (reused for permission checks, like CSAI / mcp).
stage-discussion:
	@echo "==> staging discussion + python_interface source into images/discussion/build-src"
	@rm -rf images/discussion/build-src
	@mkdir -p images/discussion/build-src
	@cp -r $(ROOT)/discussion_threaded_communication images/discussion/build-src/discussion_threaded_communication
	@cp -r $(ROOT)/python_interface images/discussion/build-src/python_interface
	@find images/discussion/build-src $(STAGE_PRUNE) -prune -exec rm -rf {} + 2>/dev/null || true

# --- folder_actions build source (staged for the fileengine-folder-actions image) ---

# The folder-actions image needs the folder_actions service + the python_interface
# gRPC client (reused for permission checks / moves, like discussion / CSAI / mcp).
stage-folder-actions:
	@echo "==> staging folder_actions + python_interface source into images/folder-actions/build-src"
	@rm -rf images/folder-actions/build-src
	@mkdir -p images/folder-actions/build-src
	@cp -r $(ROOT)/folder_actions images/folder-actions/build-src/folder_actions
	@cp -r $(ROOT)/python_interface images/folder-actions/build-src/python_interface
	@find images/folder-actions/build-src $(STAGE_PRUNE) -prune -exec rm -rf {} + 2>/dev/null || true

# --- Difference build source (staged for the fileengine-difference image) ---

# The difference image needs the difference service + the python_interface gRPC
# client (used for version content, permission checks and rendition writes, like
# CSAI / discussion / folder-actions).
#
# It also stages an ifc-rpms/ directory. That directory is normally EMPTY and the
# image builds fine that way — it exists so the COPY in the Dockerfile always has
# a source. Populate it to enable the IFC GlobalId matcher:
#   make stage-difference IFC_RPM_DIR=/path/to/ifcopenshell/rpms
# and then build that image with --build-arg INSTALL_IFC=1.
IFC_RPM_DIR ?=

stage-difference:
	@echo "==> staging difference_service + python_interface source into images/difference/build-src"
	@rm -rf images/difference/build-src
	@mkdir -p images/difference/build-src/ifc-rpms
	@cp -r $(ROOT)/difference_service images/difference/build-src/difference_service
	@cp -r $(ROOT)/python_interface images/difference/build-src/python_interface
	@if [ -n "$(IFC_RPM_DIR)" ]; then \
	  echo "==> staging IfcOpenShell RPMs from $(IFC_RPM_DIR)"; \
	  cp -v $(IFC_RPM_DIR)/*.rpm images/difference/build-src/ifc-rpms/; \
	else \
	  echo "  (no IFC_RPM_DIR set: IFC compares by geometry, not GlobalId)"; \
	fi
	@find images/difference/build-src $(STAGE_PRUNE) -prune -exec rm -rf {} + 2>/dev/null || true

# --- Share build source (staged for the fileengine-share image) -------------
#
# Three packages, and audit_service is not optional: the core attributes
# delegated activity to the link CREATOR, so the audit chain is the only record
# that an access was external. Without a publisher the service refuses to mint
# or redeem anything, so an image built without it is a dead container rather
# than a degraded one.
#
#   make stage-share
stage-audit:
	@echo "==> staging audit_service into images/audit/build-src"
	@rm -rf images/audit/build-src
	@mkdir -p images/audit/build-src
	@cp -r $(ROOT)/audit_service images/audit/build-src/audit_service
	@# Dev credentials must never reach a published image.
	@rm -f images/audit/build-src/audit_service/.env
	@find images/audit/build-src $(STAGE_PRUNE) -prune -exec rm -rf {} + 2>/dev/null || true

stage-share:
	@echo "==> staging share_service + python_interface + audit_service into images/share/build-src"
	@rm -rf images/share/build-src
	@mkdir -p images/share/build-src
	@cp -r $(ROOT)/share_service images/share/build-src/share_service
	@cp -r $(ROOT)/python_interface images/share/build-src/python_interface
	@cp -r $(ROOT)/audit_service images/share/build-src/audit_service
	@# The service's own .env carries dev credentials and must never reach a
	@# published image; compose supplies configuration at run time.
	@rm -f images/share/build-src/share_service/.env
	@find images/share/build-src $(STAGE_PRUNE) -prune -exec rm -rf {} + 2>/dev/null || true

# --- IfcOpenShell RPMs (supplied out-of-band) ------------------------------

# IfcOpenShell is not packaged for Fedora and is not vendored here, so its RPMs
# are built separately and staged in. Two images consume them:
#
#   csai        REQUIRES them — its Dockerfile installs them unconditionally, so
#               without this step `docker compose build csai` fails at COPY.
#   difference  OPTIONAL — enables the IFC GlobalId object matcher; without it
#               IFC still compares by geometry, one tier down.
#
# Build them from an IfcOpenShell checkout with its own Fedora script:
#   cd /path/to/IfcOpenShell && INSTALL_DEPS=0 ./fedora/build-rpm.sh
#   # -> artifacts in build-fedora/assets/
# then stage them here:
#   make stage-ifc IFC_RPM_DIR=/path/to/IfcOpenShell/build-fedora/assets
#
# Run AFTER stage-difference — that target wipes images/difference/build-src.
IFC_RPMS_DIR := $(CURDIR)/rpms/ifcopenshell

stage-ifc:
	@[ -n "$(IFC_RPM_DIR)" ] || { echo "!! set IFC_RPM_DIR=<dir containing IfcOpenShell RPMs>"; exit 1; }
	@ls $(IFC_RPM_DIR)/*.rpm >/dev/null 2>&1 || { echo "!! no .rpm files in $(IFC_RPM_DIR)"; exit 1; }
	@echo "==> staging IfcOpenShell RPMs from $(IFC_RPM_DIR)"
	@mkdir -p $(IFC_RPMS_DIR) images/difference/build-src/ifc-rpms
	@cp -v $(IFC_RPM_DIR)/*.rpm $(IFC_RPMS_DIR)/
	@cp $(IFC_RPM_DIR)/*.rpm images/difference/build-src/ifc-rpms/
	@echo "==> csai will now build; for difference add --build-arg INSTALL_IFC=1"

# --- Base image ------------------------------------------------------------

base-image:
	@echo "==> building base image $(BASE_IMAGE)"
	docker build -t $(BASE_IMAGE) -f images/base/Dockerfile .

# --- Publish images to a registry ------------------------------------------

# Retag every locally-built fileengine-*:$(VERSION) under $(REGISTRY) and push.
# REGISTRY is the host + namespace, WITHOUT the image name or tag, e.g.:
#   Docker Hub:  make publish REGISTRY=docker.io/acme
#   AWS ECR:     make publish REGISTRY=123456789012.dkr.ecr.us-east-1.amazonaws.com
#   GHCR:        make publish REGISTRY=ghcr.io/acme
# Authenticate first (docker login / aws ecr get-login-password | docker login ...).
publish:
	@[ -n "$(REGISTRY)" ] || { echo "!! set REGISTRY=<host/namespace>, e.g. make publish REGISTRY=docker.io/acme"; exit 1; }
	@set -eu; for c in $(IMAGES); do \
	  src=fileengine-$$c:$(VERSION); dst=$(REGISTRY)/fileengine-$$c:$(VERSION); \
	  echo "==> $$src -> $$dst"; \
	  docker image inspect $$src >/dev/null 2>&1 || { echo "  !! missing $$src (run make build + docker compose build)"; exit 1; }; \
	  docker tag $$src $$dst; \
	  docker push $$dst; \
	done
	@echo "==> published $(IMAGES) to $(REGISTRY) at :$(VERSION)"

# --- Clean -----------------------------------------------------------------

clean:
	@echo "==> removing staged artifacts"
	@rm -f $(RPMS_DIR)/*.rpm
	@rm -rf $(SPA_DIR)
