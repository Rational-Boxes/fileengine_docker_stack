#!/usr/bin/env bash
# nginx entrypoint: ensure a cert (or skip for unsecured), render the vhosts from
# BASE_DOMAIN/TLS_MODE, validate, then run nginx in the foreground.
set -e
/usr/local/bin/tls-setup.sh
/usr/local/bin/render-config.sh
nginx -t
exec nginx -g 'daemon off;'
