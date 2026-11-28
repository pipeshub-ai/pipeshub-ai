#!/usr/bin/env bash
# Certificates for the simulators' TLS fronts (docker-compose.integration.*-sim.yml of vendors whose hosts are fixed).
#
#   deployment/docker-compose/sim-tls/make-certs.sh <out-dir> [<base CA bundle>]
#
# Writes to <out-dir>:
#   tls.pem, tls.key  one certificate for every vendor host name a front answers, signed by a CA made here
#   ca-bundle.pem     <base CA bundle> plus that CA: the PipesHub container's SSL_CERT_FILE. It replaces the default
#                     trust store of httpx and OpenSSL, so it must keep the public roots the container's other
#                     outbound calls (AI providers) rely on.
# One CA and one certificate, so the overlays of several vendors can be used on one stack. The CA's key is deleted once
# the certificate is signed, so nothing else can be issued under it, and both certificates expire in two days. Never use
# the bundle outside the test stack.
set -euo pipefail

out=${1:?usage: make-certs.sh <out-dir> [<base CA bundle>]}
base=${2:-/etc/ssl/certs/ca-certificates.crt}
[ -s "$base" ] || { echo "base CA bundle $base is missing or empty" >&2; exit 1; }

hosts=(
  api.zoom.us zoom.us ssrweb.zoom.us
  api.github.com github.com uploads.github.com raw.githubusercontent.com user-images.githubusercontent.com
  private-user-images.githubusercontent.com objects.githubusercontent.com codeload.github.com
  github-production-user-asset-6210df.s3.amazonaws.com
  api.linear.app linear.app uploads.linear.app
  pipeshubsim.zendesk.com pipeshubsim.zdusercontent.com p1.zdusercontent.com
  auth.atlassian.com api.atlassian.com
)
mkdir -p "$out"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

openssl req -x509 -newkey rsa:2048 -nodes -days 2 -sha256 \
  -subj "/CN=PipesHub integration tests simulator CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -keyout "$work/ca.key" -out "$work/ca.pem" 2>/dev/null

san=$(printf 'DNS:%s,' "${hosts[@]}")
openssl req -newkey rsa:2048 -nodes -sha256 -subj "/CN=${hosts[0]}" \
  -keyout "$out/tls.key" -out "$work/tls.csr" 2>/dev/null
printf 'subjectAltName=%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n' \
  "${san%,}" > "$work/ext.cnf"
openssl x509 -req -in "$work/tls.csr" -CA "$work/ca.pem" -CAkey "$work/ca.key" -CAcreateserial \
  -days 2 -sha256 -extfile "$work/ext.cnf" -out "$out/tls.pem" 2>/dev/null

cat "$base" "$work/ca.pem" > "$out/ca-bundle.pem"
# Read by nginx and by the PipesHub process, whatever user they run as.
chmod 644 "$out/tls.pem" "$out/tls.key" "$out/ca-bundle.pem"
echo "Simulator TLS material in $out"
