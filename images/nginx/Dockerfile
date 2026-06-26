# FileEngine nginx — TLS termination, SPA serving, per-tenant subdomain routing.
#
# Serves the built SPA and reverse-proxies the tenant vhosts. The vhost config is
# rendered at startup from BASE_DOMAIN/TLS_MODE (render-config.sh); TLS is set up
# by tls-setup.sh (none / self-signed / BYO / Let's Encrypt). certbot + DNS
# plugins are bundled for issuing the wildcard via DNS-01 (obtain-cert.sh).
FROM nginx:1.27-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash openssl certbot python3-certbot-dns-cloudflare python3-certbot-dns-route53 \
    && rm -rf /var/lib/apt/lists/*

# Built SPA (staged by `make spa` into images/nginx/spa).
COPY spa/ /usr/share/nginx/html/
# Routing snippets + the websocket map (http-context).
COPY snippets/ /etc/nginx/snippets/
COPY conf.d/ /etc/nginx/conf.d/
# Startup + TLS scripts.
COPY entrypoint.sh /entrypoint.sh
COPY render-config.sh tls-setup.sh obtain-cert.sh /usr/local/bin/
RUN chmod +x /entrypoint.sh /usr/local/bin/render-config.sh \
        /usr/local/bin/tls-setup.sh /usr/local/bin/obtain-cert.sh \
    && rm -f /etc/nginx/conf.d/default.conf

EXPOSE 80 443
ENTRYPOINT ["/entrypoint.sh"]
