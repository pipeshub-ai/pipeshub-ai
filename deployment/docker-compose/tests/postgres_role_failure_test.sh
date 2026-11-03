#!/usr/bin/env bash
# A start whose role setup fails is not ready: the server and the pooler stay unhealthy (the verifier file the healthchecks require is
# removed at start and written only when the setup succeeds), a stale file from an earlier start does not vouch for it, and a service
# that waits for a healthy server (the application does) never starts. The failure is forced with PG_SCRAM_VERIFIER=/bin/false on the
# restart of a volume that is already set up. Then a start with the setup working again becomes healthy.
# Test-only environment: PROJECT_PREFIX names the compose project; EXTRA_COMPOSE_FILE adds one compose file (for example to use a
# network of your own on a host whose address pools are used up). Both change nothing when unset.
# Needs docker; PG_IMAGE_<major> names the built images (default pmcompose/postgres:<major>-pmc).
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
work="$(mktemp -d)"; proj=""; major=""
dc() { $DOCKER compose --env-file "$work/env$major" -p "$proj" -f docker-compose.yml ${EXTRA_COMPOSE_FILE:+-f "$EXTRA_COMPOSE_FILE"} "$@"; }
cleanup() { [ -n "$proj" ] && dc -f "$work/fail.yml" down -v >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
health() { $DOCKER inspect -f '{{.State.Health.Status}}' "$proj-$1-1" 2>/dev/null || echo absent; }
wait_for() { local want="$1" svc="$2"; for _ in $(seq 1 90); do [ "$(health "$svc")" = "$want" ] && return 0; sleep 2; done; return 1; }
pwvars="PG_ADMIN_PASSWORD PG_NODE_PASSWORD PG_CONNECTORS_PASSWORD PG_INDEXING_PASSWORD PG_QUERY_PASSWORD PG_JOBS_PASSWORD PG_MIGRATE_PASSWORD PG_BACKUP_PASSWORD PG_KV_PASSWORD"

for major in 17 18; do
  image_var="PG_IMAGE_$major"; image="${!image_var:-pmcompose/postgres:$major-pmc}"
  proj="${PROJECT_PREFIX:-pgimg}-rolefail-$major-$$"
  { for v in $pwvars; do echo "$v=$(pw)"; done; echo "PG_MAJOR=$major"; echo "PG_IMAGE=${image%:*}"; echo "PG_IMAGE_REV=${image##*:$major-}"; echo "REDIS_PASSWORD=x"; echo "COMPOSE_PROFILES=postgres"; } > "$work/env$major"
  # the restart with a broken role setup, and a stand-in for the application that waits for a healthy server
  cat > "$work/fail.yml" <<YML
services:
  postgres:
    environment:
      - PG_SCRAM_VERIFIER=/bin/false
    # the shipped start_period would report 'starting' instead of 'unhealthy' for five minutes
    healthcheck:
      start_period: 1s
  app-stub:
    image: $image
    entrypoint: ["sleep", "infinity"]
    healthcheck: {disable: true}
    networks: [pipeshub]
    depends_on:
      postgres:
        condition: service_healthy
YML
  dc up -d --no-build postgres pgbouncer > "$work/up.log" 2>&1 || { echo "FAIL $major: compose up failed:"; cat "$work/up.log"; fail=1; dc -f "$work/fail.yml" down -v >/dev/null 2>&1; continue; }
  wait_for healthy pgbouncer || { echo "FAIL $major: the working setup never became healthy"; fail=1; }
  check "$major: with a working role setup the server is healthy" "$(health postgres)" healthy

  # restart on the same volume with the setup broken: the stale verifier file must not make anything ready
  dc -f "$work/fail.yml" up -d --no-build --force-recreate postgres > "$work/re.log" 2>&1 || { echo "FAIL $major: recreate failed:"; cat "$work/re.log"; fail=1; }
  sleep 20
  check "$major: a failed role setup leaves no verifier file" "$($DOCKER exec "$proj-postgres-1" sh -c 'test -s /pgbouncer-auth/userlist.txt && echo present || echo absent')" absent
  wait_for unhealthy postgres && state=unhealthy || state="$(health postgres)"
  check "$major: and the server is not healthy" "$state" unhealthy
  dc restart pgbouncer >/dev/null 2>&1 || true
  sleep 15
  check "$major: and the pooler is not healthy either" "$([ "$(health pgbouncer)" = healthy ] && echo healthy || echo not-healthy)" not-healthy
  dc -f "$work/fail.yml" up -d --no-build app-stub > "$work/app.log" 2>&1 && started=yes || started=no
  check "$major: a service that waits for a healthy server does not start" "$started" no
  check "$major: its container never ran" "$([ "$($DOCKER inspect -f '{{.State.Running}}' "$proj-app-stub-1" 2>/dev/null || echo false)" = true ] && echo running || echo not-running)" not-running

  # and the same volume with the setup working again becomes healthy
  dc -f "$work/fail.yml" rm -sf app-stub >/dev/null 2>&1 || true
  dc up -d --no-build --force-recreate postgres > "$work/fix.log" 2>&1 || { echo "FAIL $major: recreate failed:"; cat "$work/fix.log"; fail=1; }
  wait_for healthy postgres && state=healthy || state="$(health postgres)"
  check "$major: a start with the setup working again becomes healthy" "$state" healthy
  dc -f "$work/fail.yml" down -v >/dev/null 2>&1
  proj=""
done
exit $fail
