#!/usr/bin/env bash
# The PostgreSQL cluster must live on the named volume on both majors: a marker written to it survives `down` (without -v) and `up`,
# and no anonymous volume is created for the container. Uses the `postgres` service of docker-compose.yml only, no ports published.
# Needs docker; PG_IMAGE_17 / PG_IMAGE_18 name the built images (default: pmcompose/postgres:17-pmc and :18-pmc).
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
pwvars="PG_ADMIN_PASSWORD PG_NODE_PASSWORD PG_CONNECTORS_PASSWORD PG_INDEXING_PASSWORD PG_QUERY_PASSWORD PG_JOBS_PASSWORD PG_MIGRATE_PASSWORD PG_BACKUP_PASSWORD"

envfile() { # major image
  { for v in $pwvars; do echo "$v=$(pw)"; done; echo "PG_MAJOR=$1"; echo "PG_IMAGE=${2%:*}"; echo "PG_IMAGE_REV=${2##*:$1-}"; echo "REDIS_PASSWORD=x"; echo "COMPOSE_PROFILES=postgres"; } > "$work/env$1"
}
dc() { $DOCKER compose --env-file "$work/env$major" -p "$proj" -f docker-compose.yml ${EXTRA_COMPOSE_FILE:+-f "$EXTRA_COMPOSE_FILE"} "$@"; }
wait_ready() { for _ in $(seq 1 60); do [ "$($DOCKER inspect -f '{{.State.Health.Status}}' "$proj-postgres-1" 2>/dev/null)" = healthy ] && return 0; sleep 2; done; return 1; }
sql() { $DOCKER exec "$proj-postgres-1" psql -U postgres -d pipeshub -tAc "$1"; }
mounts() { $DOCKER inspect -f '{{range .Mounts}}{{.Type}} {{.Destination}} {{.Name}}{{"\n"}}{{end}}' "$proj-postgres-1"; }

for major in 17 18; do
  image_var="PG_IMAGE_$major"; default="pmcompose/postgres:$major-pmc"; image="${!image_var:-$default}"
  envfile "$major" "$image"
  proj="${PROJECT_PREFIX:-pgimg}-persist-$major-$$"
  dc up -d postgres >/dev/null 2>&1; wait_ready || { echo "FAIL $major: postgres did not become healthy"; dc logs postgres | tail -5; dc down -v >/dev/null 2>&1; fail=1; continue; }
  sql "create table marker(v text); insert into marker values ('kept')" >/dev/null
  vols="$(mounts | awk '$1=="volume"{print $2" "$3}')"
  check "$major: the cluster is on the named volume ${proj}_postgres_data" "$(echo "$vols" | grep -c "^/var/lib/postgresql/data ${proj}_postgres_data$")" 1
  check "$major: no anonymous volume is mounted" "$(mounts | awk '$1=="volume" && $3 ~ /^[0-9a-f]{64}$/' | wc -l | tr -d ' ')" 0
  dc down >/dev/null 2>&1                                                              # no -v
  dc up -d postgres >/dev/null 2>&1; wait_ready
  check "$major: the marker survives down and up" "$(sql 'select v from marker')" kept
  check "$major: still no anonymous volume" "$(mounts | awk '$1=="volume" && $3 ~ /^[0-9a-f]{64}$/' | wc -l | tr -d ' ')" 0
  dc down -v >/dev/null 2>&1
done

exit $fail
