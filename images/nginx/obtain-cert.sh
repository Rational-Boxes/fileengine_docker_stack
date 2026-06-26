#!/usr/bin/env bash
# Obtain / renew the wildcard certificate via certbot DNS-01.
# Run manually (or from a cron/systemd timer) inside the nginx container:
#   docker compose exec nginx obtain-cert.sh && docker compose exec nginx nginx -s reload
#
# Wildcards REQUIRE DNS-01 (HTTP-01 can't issue them). For letsencrypt-dns the
# provider API answers the challenge automatically; for letsencrypt-manual
# certbot prints a TXT record for you to add.
set -euo pipefail
BASE_DOMAIN="${BASE_DOMAIN:?BASE_DOMAIN required}"
LE_EMAIL="${LE_EMAIL:?LE_EMAIL required}"
TLS_MODE="${TLS_MODE:-letsencrypt-dns}"

args=(certonly --non-interactive --agree-tos -m "$LE_EMAIL"
      -d "*.${BASE_DOMAIN}" -d "${BASE_DOMAIN}")
[ "${LE_STAGING:-false}" = "true" ] && args+=(--staging)

case "$TLS_MODE" in
  letsencrypt-dns)
    case "${LE_DNS_PROVIDER:?LE_DNS_PROVIDER required for letsencrypt-dns}" in
      cloudflare) args+=(--dns-cloudflare --dns-cloudflare-credentials /etc/letsencrypt/dns.ini) ;;
      route53)    args+=(--dns-route53) ;;
      *) echo "obtain-cert: unsupported LE_DNS_PROVIDER '${LE_DNS_PROVIDER}'"; exit 1 ;;
    esac ;;
  letsencrypt-manual)
    args+=(--manual --preferred-challenges dns) ;;
  *) echo "obtain-cert: TLS_MODE='$TLS_MODE' does not issue certs"; exit 1 ;;
esac

certbot "${args[@]}"
echo "obtain-cert: done — reload nginx (nginx -s reload) to pick up the new cert."
