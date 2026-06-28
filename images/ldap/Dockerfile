# 389 Directory Server + the OFFICIAL Cockpit web console (cockpit-389-ds).
#
# The upstream `389ds/dirsrv` image is openSUSE Tumbleweed, which does NOT package
# the cockpit-389-ds console plugin and can't install Cockpit cleanly. Fedora ships
# 389-ds-base, lib389 (the same `dscontainer` entrypoint the upstream image uses),
# AND cockpit + cockpit-389-ds as first-class packages — so we base on Fedora to get
# the official console. The instance still lives under /data (lib389 container mode),
# so the `ldapdata` volume layout stays compatible with the upstream image.
FROM fedora:41

# 389-ds-base + lib389 (provides /usr/lib/dirsrv/dscontainer and the dirsrv user);
# cockpit-ws/bridge + the official 389 console plugin; openldap-clients for the
# healthcheck's ldapsearch. Weak deps off to keep the image lean.
# 389-ds-base pulls python3-lib389 (which provides dscontainer + the dirsrv user);
# cockpit-ws/bridge + the official 389 console plugin; openldap-clients for the
# healthcheck's ldapsearch.
RUN dnf -y install --setopt=install_weak_deps=False \
        389-ds-base \
        cockpit-ws cockpit-bridge cockpit-389-ds \
        openldap-clients procps-ng shadow-utils passwd \
    && dnf clean all && rm -rf /var/cache/dnf

# Persist the instance across container RECREATION. dscontainer stores db/logs/run/
# bak/ldif under /data, but the instance's config + ssca dirs live at
# /etc/dirsrv/slapd-localhost — which is NOT the volume. The upstream image solves
# this by symlinking those into /data at build time (dscontainer's own notes say it
# can only be done in the image, not at runtime). Without this, dse.ldif lives only
# in the container's writable layer and is LOST whenever the container is recreated
# (e.g. an image upgrade), corrupting the directory. Instance serverid defaults to
# "localhost" (matching dscontainer + the upstream image).
RUN mkdir -p /data/config /data/ssca /etc/dirsrv \
    && ln -sf /data/config /etc/dirsrv/slapd-localhost \
    && ln -sf /data/ssca   /etc/dirsrv/ssca

# 3389/3636 = LDAP/LDAPS (unprivileged, as the upstream image); 9090 = Cockpit.
EXPOSE 3389 3636 9090
VOLUME /data

COPY images/ldap/entrypoint.sh /usr/local/bin/ldap-entrypoint.sh
RUN chmod +x /usr/local/bin/ldap-entrypoint.sh

# Starts Cockpit (when COCKPIT_ADMIN_PASSWORD is set) then execs dscontainer, which
# creates/runs the instance from DS_* env into /data — same lib389 tool the upstream
# image uses (Fedora ships it at /usr/libexec/dirsrv/dscontainer).
ENTRYPOINT ["/usr/local/bin/ldap-entrypoint.sh"]
CMD ["/usr/libexec/dirsrv/dscontainer", "-r"]
