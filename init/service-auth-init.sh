#!/usr/bin/env bash
# Issue one credential per service, and grant each the capabilities it needs.
#
# Runs once, after the core has created its schema. It writes each secret into a
# shared volume that the services mount read-only, because a one-shot cannot
# inject environment variables into containers Compose has already defined — the
# tokens simply do not exist at `compose up` time. See
# PROPOSAL_service_authentication.md §3.7.
#
# It talks to PostgreSQL, not to gRPC. Issuance is a database write hashed under
# the pepper, so this container needs the same DB credentials and the same
# pepper as the core, and needs no identity of its own.
set -euo pipefail

TOKEN_DIR=${TOKEN_DIR:-/run/fileengine/tokens}
mkdir -p "$TOKEN_DIR"

if [ -z "${FILEENGINE_SERVICE_TOKEN_PEPPER:-}" ]; then
    echo "FILEENGINE_SERVICE_TOKEN_PEPPER is not set." >&2
    echo "Set SERVICE_TOKEN_PEPPER in .env — credentials are stored as" >&2
    echo "HMAC(secret, pepper), so without it nothing this writes can be verified." >&2
    exit 1
fi

# The capability sets, derived rather than guessed.
#
# PROPOSAL §6.2 warns that writing this matrix from intuition produces one that
# is wrong in the permissive direction. So each set below is what the service is
# observed to call: every core RPC reachable from its source, mapped through the
# core's compiled method->capability table. Two entries are deliberately NARROWER
# than what the code calls, and both are the proposal's explicit decisions:
#
#   http_bridge  calls PurgeOldVersions (grpc_client_wrapper.cpp:189) but is not
#                granted `destroy`. Its purge endpoint will refuse. §5.4.9 keeps
#                erasure on the admin surface; this makes that a property of the
#                core rather than a convention.
#   mcp          is granted neither `delete` nor `destroy` per §6.3. "Append
#                only, recoverable" stops being something the MCP service chooses
#                and becomes something the core enforces — a prompt-injected
#                agent cannot delete however convincingly it is told to. The
#                door's tool surface is cut to the same set: it used to expose a
#                delete tool and a show_deleted listing that this matrix could
#                only ever refuse. It DOES hold `restore`: RestoreToVersion
#                inserts a new version row pointing at the older payload and
#                removes nothing, so it is a write — and it is the agent's only
#                way back from a bad one.
#
# Both are behaviour changes, not oversights. Revisit them here, deliberately, if
# the endpoints are wanted back.
declare -A CAPS=(
    [http_bridge]="read write delete restore acl roles admin"
    [webdav_bridge]="read write delete restore"
    [csai]="read write delete"
    [mcp]="read write restore"
    [discussion]="read write"
    [folder_actions]="read write acl"
    [difference]="read write delete"
    [share]="read write"
    [bcf]="read write"
    [audit_service]="accountability"
)

issued=0
skipped=0
for service in "${!CAPS[@]}"; do
    token_file="$TOKEN_DIR/$service"

    # Idempotent, and checked against the database rather than the file alone.
    # A token file left behind by a volume that outlived its database would
    # otherwise be handed to a service that cannot authenticate with it, and the
    # failure would surface as UNAUTHENTICATED at runtime with nothing to say why.
    if [ -s "$token_file" ] && fileengine_cli service-token list 2>/dev/null \
            | awk '{print $1}' | grep -qx "$service"; then
        skipped=$((skipped + 1))
    else
        # stdout is the secret and nothing else — the CLI silences the logger for
        # exactly this. Write via a temporary file so a service never reads a
        # half-written token.
        # issue or rotate, and the difference matters. `issue` REFUSES to replace a
        # credential in place — deliberately, because replacing would strand every
        # running instance still presenting the old secret. So if the core already
        # knows this service but the volume has no token for it (a wiped volume, an
        # interrupted first run), issuing fails forever and the stack never starts.
        #
        # `rotate` adds a secret ALONGSIDE the existing one. Both stay valid, so
        # anything still holding the old token keeps working until it restarts onto
        # the new one. Retire the old with `service-token prune <service>` once
        # everything has rolled over.
        verb=issue
        if fileengine_cli service-token list 2>/dev/null | awk '{print $1}' | grep -qx "$service"; then
            verb=rotate
            echo "  $service: credential exists but no token file — rotating"
        fi

        tmp="$token_file.tmp"
        # Clear a temp left by a run that died between write and move.
        rm -f "$tmp"
        if ! fileengine_cli service-token "$verb" "$service" > "$tmp"; then
            echo "could not issue a credential for $service" >&2
            rm -f "$tmp"
            exit 1
        fi
        # 0444, not 0400. Ten images run as ten different unprivileged users, and a
        # mode only this container's uid can read is a mode no service can use. The
        # containment here is the volume: it is internal to this stack, never
        # published, and mounted read-only by exactly the services that need it. A
        # tighter mode would need a uid shared across every image, which is a larger
        # change than it sounds and buys nothing against anyone who can already
        # enter one of these containers.
        chmod 0444 "$tmp"
        mv "$tmp" "$token_file"
        issued=$((issued + 1))
    fi

    # Capabilities are reconciled on EVERY run, not only when a credential is
    # first issued. `service grant` is idempotent; issuance is not, so granting
    # only in the branch above meant a change to the matrix at the top of this
    # file never reached a stack that had already started once. The service kept
    # whatever it was born with and failed the new calls with PERMISSION_DENIED,
    # several layers away from the edit that caused it.
    #
    # This adds; it does not take away. A capability dropped from the matrix must
    # be revoked deliberately (`fileengine_cli service revoke-cap`), because an
    # operator may have armed this deployment by hand.
    for cap in ${CAPS[$service]}; do
        # `accountability` and `destroy` refuse to be granted without this flag,
        # so that arming a service stays a separate act from onboarding it. Only
        # audit_service asks for one, and it is the reader the capability was
        # defined for. `destroy` is granted to nothing.
        flag=""
        case "$cap" in
            accountability|destroy) flag="--i-understand-this-is-high-risk" ;;
        esac
        # Not `|| true`, and not silenced. A grant that fails quietly leaves the
        # service able to authenticate but unable to work, and the symptom shows
        # up much later as PERMISSION_DENIED with nothing pointing back here.
        fileengine_cli service grant "$service" "$cap" $flag
    done
done

echo "service auth: issued $issued, already present $skipped"

# The tokens are readable by the services that mount this volume. The volume is
# not published anywhere and each mount is read-only; the secrets are outside
# container metadata, which is the point of the file over an env var.
ls -1 "$TOKEN_DIR"
