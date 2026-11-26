#!/usr/bin/env bash
# A role setup that fails is retried: the server and the pooler stay unhealthy while it fails, and become healthy on their own, in the
# same container (no restart), once the cause clears. The failure is a stand-in for the image's role script (PG_ROLES_SCRIPT) that
# fails until a marker file exists and then runs the real one. It is used on the restart of a volume that is already set up (the first
# start creates the roles while initialising the cluster, which this does not cover).
# Test-only environment: PROJECT_PREFIX names the compose project; EXTRA_COMPOSE_FILE adds one compose file. Both change nothing when unset.
# Needs docker; PG_IMAGE_<major> names the built images (default pmcompose/postgres:<major>-pmc).
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
work="$(mktemp -d)"; proj=""; major=""
dc() { $DOCKER compose --env-file "$work/env$major" -p "$proj" -f docker-compose.yml ${EXTRA_COMPOSE_FILE:+-f "$EXTRA_COMPOSE_FILE"} ${WITH_STANDIN:+-f "$work/recover.yml"} "$@"; }
cleanup() { [ -n "$proj" ] && dc down -v >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
health() { $DOCKER inspect -f '{{.State.Health.Status}}' "$proj-$1-1" 2>/dev/null || echo absent; }
wait_for() { local want="$1" svc="$2"; for _ in $(seq 1 90); do [ "$(health "$svc")" = "$want" ] && return 0; sleep 2; done; return 1; }
pwvars="PG_ADMIN_PASSWORD PG_NODE_PASSWORD PG_CONNECTORS_PASSWORD PG_INDEXING_PASSWORD PG_QUERY_PASSWORD PG_JOBS_PASSWORD PG_MIGRATE_PASSWORD PG_BACKUP_PASSWORD PG_KV_PASSWORD"

cat > "$work/standin.sh" <<'EOS'
#!/bin/sh
[ -e /tmp/roles-can-run ] || { echo "standin: role setup not possible yet" >&2; exit 1; }
exec /usr/local/bin/pg-roles.sh
EOS
chmod 755 "$work/standin.sh"

for major in 17 18; do
  image_var="PG_IMAGE_$major"; image="${!image_var:-pmcompose/postgres:$major-pmc}"
  proj="${PROJECT_PREFIX:-pgimg}-rolerecover-$major-$$"
  { for v in $pwvars; do echo "$v=$(pw)"; done; echo "PG_MAJOR=$major"; echo "PG_IMAGE=${image%:*}"; echo "PG_IMAGE_REV=${image##*:$major-}"; echo "REDIS_PASSWORD=x"; echo "COMPOSE_PROFILES=postgres"; } > "$work/env$major"
  cat > "$work/recover.yml" <<YML
services:
  postgres:
    environment:
      - PG_ROLES_SCRIPT=/standin.sh
      - PG_ROLES_RETRY_SECS=2
    volumes:
      - $work/standin.sh:/standin.sh:ro
    # the shipped start_period would report 'starting' instead of 'unhealthy' for five minutes
    healthcheck:
      start_period: 1s
YML
  dc up -d --no-build postgres pgbouncer > "$work/up.log" 2>&1 || { echo "FAIL $major: compose up failed:"; cat "$work/up.log"; fail=1; dc down -v >/dev/null 2>&1; continue; }
  wait_for healthy pgbouncer || { echo "FAIL $major: the first start never became healthy"; fail=1; }
  # restart on the same volume with the stand-in, which cannot set up the roles yet
  export WITH_STANDIN=1
  dc up -d --no-build --force-recreate postgres > "$work/re.log" 2>&1 || { echo "FAIL $major: recreate failed:"; cat "$work/re.log"; fail=1; }
  wait_for unhealthy postgres && state=unhealthy || state="$(health postgres)"
  check "$major: while role setup fails the server is not healthy" "$state" unhealthy
  sleep 10
  check "$major: and no verifier file exists" "$($DOCKER exec "$proj-postgres-1" sh -c 'test -s /pgbouncer-auth/userlist.txt && echo present || echo absent')" absent
  tries="$($DOCKER logs "$proj-postgres-1" 2>&1 | grep -c 'role setup failed' || true)"
  check "$major: and each attempt is logged (more than one)" "$([ "$tries" -gt 1 ] && echo yes || echo "no ($tries)")" yes
  started="$($DOCKER inspect -f '{{.State.StartedAt}}' "$proj-postgres-1")"

  # the cause clears: no restart, the next attempt succeeds
  $DOCKER exec "$proj-postgres-1" touch /tmp/roles-can-run
  wait_for healthy postgres && state=healthy || state="$(health postgres)"
  check "$major: once the cause clears the server becomes healthy" "$state" healthy
  check "$major: the verifier file appears" "$($DOCKER exec "$proj-postgres-1" sh -c 'test -s /pgbouncer-auth/userlist.txt && echo present || echo absent')" present
  check "$major: without a restart of the container" "$($DOCKER inspect -f '{{.State.StartedAt}}' "$proj-postgres-1")" "$started"
  wait_for healthy pgbouncer && state=healthy || state="$(health pgbouncer)"
  check "$major: and the pooler becomes healthy" "$state" healthy
  dc down -v >/dev/null 2>&1
  unset WITH_STANDIN; proj=""
done
exit $fail
