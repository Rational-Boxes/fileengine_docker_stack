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

    # DO NOT clobber a config the operator supplied. The defaults below assume
    # the console gets its OWN hostname (ldap-admin.<base>, UrlRoot=/), which is
    # how the compose stack runs it. A deployment that instead proxies the
    # console at a sub-path must set UrlRoot and Origins to match, and the only
    # way to do that is to mount a cockpit.conf.
    #
    # Unconditionally writing this file broke that outright: the Ansible
    # openldap role bind-mounts its sub-path version READ-ONLY, the redirect
    # failed with "Read-only file system", and because this script runs under
    # `set -e` the container exited 1 and crash-looped — so the DIRECTORY SERVER
    # never started, over a console config file. Guarding the write keeps the
    # image's defaults for everyone who does not care, and makes a mounted
    # config authoritative for everyone who does.
    if [ -f /etc/cockpit/cockpit.conf ]; then
        echo "ldap: /etc/cockpit/cockpit.conf already present — keeping it"
    else
        cat > /etc/cockpit/cockpit.conf <<EOF
[WebService]
AllowUnencrypted = true
ProtocolHeader = X-Forwarded-Proto
Origins = https://ldap-admin.${base} http://ldap-admin.${base}
UrlRoot = /
EOF
    fi

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
