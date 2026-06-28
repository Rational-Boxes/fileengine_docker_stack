#!/bin/sh
# Bring up the OFFICIAL 389 Cockpit console alongside the directory server.
#
# Cockpit's normal PAM-login flow needs systemd (socket-activated cockpit-session),
# which a container doesn't have. So we run cockpit-ws in `--local-session` mode:
# it launches the bridge directly as root (no Cockpit login page), and the
# cockpit-389-ds plugin manages the LOCAL instance with full privilege. Access
# control is therefore enforced at the EDGE — nginx fronts the console at
# ldap-admin.<base> with TLS + HTTP Basic auth (see images/nginx/snippets/
# ldap-admin.conf); the console is never published directly. cockpit.conf trusts
# the proxy's X-Forwarded-Proto, permits the plain-HTTP internal hop, and allowlists
# the public origin so the WebSocket isn't rejected. Finally exec dscontainer (the
# real CMD), which owns PID 1 and the instance lifecycle as the upstream image.
set -e

start_cockpit() {
    # COCKPIT_ADMIN_PASSWORD presence is the on/off switch (the value itself is used
    # by nginx for Basic auth, not here). Unset → console not started.
    [ -n "${COCKPIT_ADMIN_PASSWORD:-}" ] || { echo "ldap: COCKPIT_ADMIN_PASSWORD unset — Cockpit console disabled"; return; }
    command -v cockpit-bridge >/dev/null 2>&1 || { echo "ldap: cockpit not installed — console disabled"; return; }

    base="${BASE_DOMAIN:-example.com}"
    mkdir -p /etc/cockpit
    cat > /etc/cockpit/cockpit.conf <<EOF
[WebService]
AllowUnencrypted = true
ProtocolHeader = X-Forwarded-Proto
Origins = https://ldap-admin.${base} http://ldap-admin.${base}
UrlRoot = /
EOF

    ws=""
    for c in /usr/libexec/cockpit-ws /usr/lib/cockpit/cockpit-ws /usr/sbin/cockpit-ws; do
        [ -x "$c" ] && { ws="$c"; break; }
    done
    [ -n "$ws" ] || { echo "ldap: cockpit-ws binary not found — console disabled"; return; }

    echo "ldap: starting 389 Cockpit console on :9090 (local-session; auth+TLS enforced by nginx at ldap-admin.${base})"
    "$ws" --local-session=/usr/bin/cockpit-bridge --no-tls --port 9090 --address 0.0.0.0 &
}

start_cockpit
exec "$@"
