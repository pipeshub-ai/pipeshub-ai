#!/usr/bin/env bash
# A fresh install's metrics exporters log in with nothing set by hand: install.sh writes the .env (with PG_MONITOR_PASSWORD), and the
# postgres, pgbouncer and both exporters of the postgres-metrics profile start from it. Each exporter must report that it reached its
# target (pg_up 1, pgbouncer_up 1), which needs the pipeshub_monitor login the server creates from that password.
# Test-only environment: PROJECT_PREFIX names the compose projects; EXTRA_COMPOSE_FILE adds one compose file (for example to use a
# network of your own on a host whose address pools are used up). Both change nothing when unset.
# Needs docker; PG_IMAGE_17 / PG_IMAGE_18 name the built images (default: pmcompose/postgres:17-pmc and :18-pmc).
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
work="$(mktemp -d)"; proj=""
cleanup() { if [ -n "$proj" ]; then $DOCKER compose --env-file "$work/.env" -p "$proj" -f docker-compose.yml ${EXTRA_COMPOSE_FILE:+-f "$EXTRA_COMPOSE_FILE"} down -v >/dev/null 2>&1 || true; fi; rm -rf "$work"; }
trap cleanup EXIT
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
dc() { $DOCKER compose --env-file "$work/.env" -p "$proj" -f docker-compose.yml ${EXTRA_COMPOSE_FILE:+-f "$EXTRA_COMPOSE_FILE"} "$@"; }
# The value of an exporter's "up" gauge, polled until it is 1 (the exporters retry their target on every scrape).
up_gauge() { # container port metric
  local v=""
  for _ in $(seq 1 30); do
    v="$($DOCKER exec "$1" wget -qO- "http://127.0.0.1:$2/metrics" 2>/dev/null | awk -v m="$3" '$1==m{print $2}')"
    [ "$v" = 1 ] && break; sleep 2
  done
  echo "${v:-none}"
}

for major in 17 18; do
  image_var="PG_IMAGE_$major"; default="pmcompose/postgres:$major-pmc"; image="${!image_var:-$default}"
  proj="${PROJECT_PREFIX:-pgimg}-metrics-$major-$$"
  rm -rf "$work/install"; mkdir -p "$work/install"
  cp install.sh docker-compose.yml env.template "$work/install/"
  if ! (cd "$work/install" && PIPESHUB_PROJECT="$proj" bash ./install.sh --yes --print-env-only > "$work/install.log" 2>&1); then
    echo "FAIL $major: install.sh --print-env-only failed:"; tail -20 "$work/install.log"; fail=1; continue
  fi
  check "$major: install.sh writes a monitoring password" "$(grep -cE '^PG_MONITOR_PASSWORD=.+' "$work/install/.env")" 1
  # Test-only: the image under test, and the exporters' host ports left to docker so parallel runs do not collide.
  { grep -vE '^(PG_MAJOR|COMPOSE_PROFILES)=' "$work/install/.env"
    echo "PG_MAJOR=$major"; echo "PG_IMAGE=${image%:*}"; echo "PG_IMAGE_REV=${image##*:$major-}"
    echo "COMPOSE_PROFILES=postgres,postgres-metrics"; echo "PG_EXPORTER_PORT=0"; echo "PGBOUNCER_EXPORTER_PORT=0"; } > "$work/.env"

  if ! dc up -d --no-build postgres pgbouncer postgres-exporter pgbouncer-exporter > "$work/up.log" 2>&1; then
    echo "FAIL $major: compose up failed:"; cat "$work/up.log"; fail=1; dc down -v >/dev/null 2>&1 || true; continue
  fi
  check "$major: the server exporter logs in (pg_up)" "$(up_gauge "$proj-postgres-exporter-1" 9187 pg_up)" 1
  check "$major: the pooler exporter logs in (pgbouncer_up)" "$(up_gauge "$proj-pgbouncer-exporter-1" 9127 pgbouncer_up)" 1
  check "$major: the monitoring login is a member of pg_monitor only" \
    "$($DOCKER exec "$proj-postgres-1" psql -U postgres -d pipeshub -tAc "select string_agg(r.rolname, ',') from pg_auth_members m join pg_roles r on r.oid=m.roleid join pg_roles u on u.oid=m.member where u.rolname='pipeshub_monitor'")" pg_monitor
  dc down -v >/dev/null 2>&1
done

exit $fail
