#!/usr/bin/env bash
# vhost-certs.sh <dir>: a throwaway CA and a MinIO certificate for the
# virtual-hosted S3 leg of the storage tests (see storage.yml). Writes ca.crt,
# public.crt and private.key into <dir>. The certificate covers the S3 host
# names the adapter uses and the loopback address MinIO is published on.
set -euo pipefail
dir=${1:?usage: vhost-certs.sh <dir>}
mkdir -p "$dir"
cd "$dir"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=storage-it test CA" \
  -keyout ca.key -out ca.crt 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=s3.us-east-1.amazonaws.com" \
  -keyout private.key -out minio.csr 2>/dev/null
printf 'subjectAltName=DNS:s3.us-east-1.amazonaws.com,DNS:*.s3.us-east-1.amazonaws.com,IP:127.0.0.1\n' > san.ext
openssl x509 -req -in minio.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 2 \
  -extfile san.ext -out public.crt 2>/dev/null
rm -f minio.csr san.ext ca.key ca.srl
# The MinIO container runs as an unprivileged user.
chmod 644 ca.crt public.crt private.key
