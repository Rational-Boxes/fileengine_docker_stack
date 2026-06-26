#!/usr/bin/env bash
# Ensure a TLS cert exists for nginx (or skip entirely for the unsecured mode).
set -euo pipefail
TLS_MODE="${TLS_MODE:-selfsigned}"

if [ "$TLS_MODE" = "none" ]; then
  echo "tls: TLS_MODE=none — UNSECURED HTTP, no certificate"
  exit 0
fi

BASE_DOMAIN="${BASE_DOMAIN:?BASE_DOMAIN required}"
CERT_DIR="${TLS_CERT_DIR:-/etc/nginx/tls}"
LE_DIR="/etc/letsencrypt/live/${BASE_DOMAIN}"
mkdir -p "$CERT_DIR"

have_cert() { [ -s "$CERT_DIR/fullchain.pem" ] && [ -s "$CERT_DIR/privkey.pem" ]; }
gen_selfsigned() {
  echo "tls: generating self-signed wildcard cert for *.${BASE_DOMAIN}"
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout "$CERT_DIR/privkey.pem" -out "$CERT_DIR/fullchain.pem" \
    -subj "/CN=*.${BASE_DOMAIN}" \
    -addext "subjectAltName=DNS:*.${BASE_DOMAIN},DNS:${BASE_DOMAIN}"
}
link_le() {
  ln -sf "$LE_DIR/fullchain.pem" "$CERT_DIR/fullchain.pem"
  ln -sf "$LE_DIR/privkey.pem"  "$CERT_DIR/privkey.pem"
}

case "$TLS_MODE" in
  byo)
    have_cert || { echo "tls: TLS_MODE=byo but no cert in $CERT_DIR (mount fullchain.pem + privkey.pem)"; exit 1; }
    echo "tls: using operator-provided cert in $CERT_DIR" ;;
  letsencrypt-dns|letsencrypt-manual)
    if [ -s "$LE_DIR/fullchain.pem" ]; then
      link_le; echo "tls: using Let's Encrypt cert for ${BASE_DOMAIN}"
    else
      gen_selfsigned
      echo "tls: no Let's Encrypt cert yet — self-signed bootstrap so nginx can start."
      echo "tls: issue the wildcard with obtain-cert.sh (DNS-01, TLS_MODE=$TLS_MODE), then reload."
    fi ;;
  selfsigned|*)
    have_cert || gen_selfsigned ;;
esac
