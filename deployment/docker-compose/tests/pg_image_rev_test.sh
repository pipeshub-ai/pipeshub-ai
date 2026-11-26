#!/usr/bin/env bash
# No Docker: the PostgreSQL image's tag revision moves with what the image is built from. Compose builds the image only when its tag
# (${PG_MAJOR}-${PG_IMAGE_REV}) is missing, so a changed build context under an unchanged revision leaves an existing install on the
# old image after `docker compose up -d` (postgres/image-rev.txt).
set -euo pipefail
cd "$(dirname "$0")/.."
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
read -r rev recorded < <(grep -v '^#' postgres/image-rev.txt)
hash="$(cd postgres && find . -type f ! -name README.md ! -name image-rev.txt | LC_ALL=C sort | xargs sha256sum | sha256sum | cut -d' ' -f1)"
check "the build context is the one revision $rev was recorded for (else bump PG_IMAGE_REV and record the new hash)" "$hash" "$recorded"
pins="$(grep -ho 'pipeshubai/postgres}:${PG_MAJOR:-18}-${PG_IMAGE_REV:-[0-9]*}' docker-compose*.yml | sed -E 's/.*PG_IMAGE_REV:-([0-9]+)\}/\1/' | sort -u)"
check "every compose file pins revision $rev" "$pins" "$rev"
check "some compose file pins the image at all" "$([ -n "$pins" ] && echo yes)" yes

# an image built before the backup metrics, under a newer docker-compose.postgres-backup.yml: the service says so at start and is unhealthy
cmd="$(sed -nE 's/^    command: \["sh", "-c", "(.*)"\]$/\1/p' docker-compose.postgres-backup.yml)"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
printf '#!/bin/sh\necho scheduler started\n' > "$work/pg-backup.sh"; chmod +x "$work/pg-backup.sh"
out="$(PATH="$work:$PATH" sh -c "$cmd" 2>&1)"
if [ -x /usr/local/bin/pg-backup-metrics.pl ]; then echo "skip stale-image message: this host has /usr/local/bin/pg-backup-metrics.pl"
else
  check "a stale image is named at start, and the scheduler still starts" \
    "$(grep -c 'STALE IMAGE: /usr/local/bin/pg-backup-metrics.pl is missing' <<<"$out"):$(tail -1 <<<"$out")" "1:scheduler started"
fi
check "a stale image fails the healthcheck" "$(grep -c "&& test -x /usr/local/bin/pg-backup-metrics.pl']" docker-compose.postgres-backup.yml)" 1
exit "$fail"
