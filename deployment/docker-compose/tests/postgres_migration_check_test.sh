#!/usr/bin/env bash
# The repository's own migration and security-surface check (backend/db, `python -m db.migrate`, exit 4 on a violation) run against
# a fresh database made by the PostgreSQL image: it must exit 0, twice, and leave kernel.rls_violations empty. This is what stops the
# image's role setup and the schema from drifting apart (a stray extension or a missing login makes the check refuse the database).
# Test-only environment: PROJECT_PREFIX names the compose project; EXTRA_COMPOSE_FILE adds one compose file (for example to use a
# network of your own on a host whose address pools are used up). Both change nothing when unset.
# Needs docker; PG_IMAGE_<major> names the built postgres images (default pmcompose/postgres:<major>-pmc), MIGRATE_IMAGE the migrate
# image (default: built here from backend/db/Dockerfile as pmcompose/migrate:pmc).
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
work="$(mktemp -d)"; proj=""; major=""
dc() { $DOCKER compose --env-file "$work/env$major" -p "$proj" -f docker-compose.yml ${EXTRA_COMPOSE_FILE:+-f "$EXTRA_COMPOSE_FILE"} "$@"; }
cleanup() { [ -n "$proj" ] && dc down -v >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
pwvars="PG_ADMIN_PASSWORD PG_NODE_PASSWORD PG_CONNECTORS_PASSWORD PG_INDEXING_PASSWORD PG_QUERY_PASSWORD PG_JOBS_PASSWORD PG_MIGRATE_PASSWORD PG_BACKUP_PASSWORD PG_KV_PASSWORD"
migrate_image="${MIGRATE_IMAGE:-pmcompose/migrate:pmc}"
if [ -z "${MIGRATE_IMAGE:-}" ]; then $DOCKER build -q -f ../../backend/db/Dockerfile -t "$migrate_image" ../.. >/dev/null; fi

for major in 17 18; do
  image_var="PG_IMAGE_$major"; image="${!image_var:-pmcompose/postgres:$major-pmc}"
  proj="${PROJECT_PREFIX:-pgimg}-migcheck-$major-$$"
  { for v in $pwvars; do echo "$v=$(pw)"; done; echo "PG_MAJOR=$major"; echo "PG_IMAGE=${image%:*}"; echo "PG_IMAGE_REV=${image##*:$major-}"
    echo "REDIS_PASSWORD=x"; echo "COMPOSE_PROFILES=postgres"; } > "$work/env$major"
  . "$work/env$major"
  if ! dc up -d --no-build --wait postgres > "$work/up.log" 2>&1; then echo "FAIL $major: compose up failed:"; cat "$work/up.log"; fail=1; dc down -v >/dev/null 2>&1; continue; fi
  net="$($DOCKER inspect -f '{{range $k, $_ := .NetworkSettings.Networks}}{{$k}}{{end}}' "$proj-postgres-1")"
  migrate() { $DOCKER run --rm --network "$net" -e "PG_MIGRATE_URL=postgresql+psycopg://pipeshub_migrate:$PG_MIGRATE_PASSWORD@postgres:5432/pipeshub" "$migrate_image" 2>&1; }
  migrate > "$work/first.log" && rc=0 || rc=$?
  check "$major: the migration and surface check exit 0 on a fresh database" "$rc" 0
  [ "$rc" -eq 0 ] || tail -15 "$work/first.log"
  migrate > "$work/second.log" && rc=0 || rc=$?
  check "$major: and exit 0 again on a database at head" "$rc" 0
  check "$major: kernel.rls_violations is empty" "$($DOCKER exec "$proj-postgres-1" psql -U postgres -d pipeshub -tAc 'select count(*) from kernel.rls_violations')" 0
  dc down -v >/dev/null 2>&1
  proj=""
done
exit $fail
