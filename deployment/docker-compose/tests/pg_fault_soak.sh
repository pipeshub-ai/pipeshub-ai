#!/usr/bin/env bash
# The long fault soak of the PostgreSQL-only stack (not run on pull requests). Starts postgres, pgbouncer, the migration job and the
# placement and fold workers of docker-compose.postgres-only.yml (JIT off, ports published on 127.0.0.1), then runs
# backend/python/tests/integration/pg_fault/soak.py against them: steady connector writes with their record events, a queue consumer
# with the consumer inbox, the typed-graph worker, configuration reads and writes and permission reads, with a fault every cycle
# (server restart fast and immediate, pooler restart, every service role's sessions terminated, the server away for 60 s). Each cycle's
# checks and a sample a minute (connections, WAL, database size, memory, restarts) go to $OUT as JSON lines; the exit code is the driver's.
#
#   SOAK_HOURS=6 OUT=/tmp/soak.jsonl PYTHON=<python with backend/python's dependencies> deployment/docker-compose/tests/pg_fault_soak.sh
#
# PG_MAJOR (default 17), PG_IMAGE_<major> (default: built here from ./postgres as pmcompose/postgres:<major>-pmc), MIGRATE_IMAGE (default:
# built from backend/db/Dockerfile as pmcompose/migrate:pmc), PROJECT (default pgres-soak-<pid>-<random>, printed; containers are named
# <PROJECT>-<service>-1; a project that already has containers or volumes is refused, as the cleanup removes its volumes),
# CYCLE_MIN (default 15), KEEP=1 leaves the project up.
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
major="${PG_MAJOR:-17}"
proj="${PROJECT:-pgres-soak-$$-$RANDOM}"
# before the cleanup trap: its down -v would remove the volumes of a run that is still going or was kept
label="label=com.docker.compose.project=$proj"
# each query on its own line: a failure inside $(...) as an argument of [ does not trip errexit, and would read as "no such project"
containers="$($DOCKER ps -aq --filter "$label")" || { echo "cannot list Docker containers; not starting" >&2; exit 1; }
volumes="$($DOCKER volume ls -q --filter "$label")" || { echo "cannot list Docker volumes; not starting" >&2; exit 1; }
if [ -n "$containers$volumes" ]; then
  echo "the Compose project $proj already exists; name another with PROJECT" >&2; exit 1
fi
echo "Compose project: $proj"
out="$(realpath -m "${OUT:?OUT names the JSON lines file}")"
py="${PYTHON:-python3}"
work="$(mktemp -d)"
env_file="$work/env"
dc() { $DOCKER compose --env-file "$env_file" -p "$proj" -f docker-compose.yml -f docker-compose.postgres-only.yml -f "$work/soak.yml" "$@"; }
cleanup() { if [ "${KEEP:-0}" != 1 ]; then dc down -v >/dev/null 2>&1 || true; fi; rm -rf "$work"; }
trap cleanup EXIT
trap 'exit 143' INT TERM
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

image_var="PG_IMAGE_$major"; image="${!image_var:-pmcompose/postgres:$major-pmc}"
if [ -z "${!image_var:-}" ]; then $DOCKER build -q --build-arg "PG_MAJOR=$major" -t "$image" postgres >/dev/null; fi
migrate_image="${MIGRATE_IMAGE:-pmcompose/migrate:pmc}"
if [ -z "${MIGRATE_IMAGE:-}" ]; then $DOCKER build -q -f ../../backend/db/Dockerfile -t "$migrate_image" ../.. >/dev/null; fi

declare -A pass
for v in ADMIN NODE CONNECTORS INDEXING QUERY KV JOBS MIGRATE BACKUP; do pass[$v]="$(pw)"; echo "PG_${v}_PASSWORD=${pass[$v]}"; done > "$env_file"
{ echo "PG_MAJOR=$major"; echo "PG_IMAGE=${image%:*}"; echo "PG_IMAGE_REV=${image##*:$major-}"
  echo "PIPESHUB_MIGRATE_IMAGE=${migrate_image%:*}"; echo "IMAGE_TAG=${migrate_image##*:}"; echo "PG_MEMORY_LIMIT=4G"; } >> "$env_file"
# fixed host ports: an ephemeral one would change when a fault restarts the container
free_port() { python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
pg_port="$(free_port)"; pgb_port="$(free_port)"
cat > "$work/soak.yml" <<YML
services:
  postgres:
    command: ["postgres", "-c", "jit=off"]
    ports: ["127.0.0.1:$pg_port:5432"]
  pgbouncer:
    ports: ["127.0.0.1:$pgb_port:6432"]
YML

dc up -d --no-build postgres pgbouncer pipeshub-migrate pipeshub-placement pipeshub-fold
for _ in $(seq 120); do [ "$($DOCKER inspect -f '{{.State.Status}}' "$proj-pipeshub-migrate-1")" = exited ] && break; sleep 2; done
[ "$($DOCKER inspect -f '{{.State.ExitCode}}' "$proj-pipeshub-migrate-1")" = 0 ] || { echo "the migration job failed"; dc logs pipeshub-migrate | tail -40; exit 1; }

PG_SOAK_PASSWORDS="$(printf '{"admin":"%s","node":"%s","connectors":"%s","indexing":"%s","query":"%s","kv":"%s","jobs":"%s"}' \
  "${pass[ADMIN]}" "${pass[NODE]}" "${pass[CONNECTORS]}" "${pass[INDEXING]}" "${pass[QUERY]}" "${pass[KV]}" "${pass[JOBS]}")" \
PG_SOAK_SERVER_PORT="$pg_port" PG_SOAK_POOLER_PORT="$pgb_port" \
PG_SOAK_PG_CONTAINER="$proj-postgres-1" PG_SOAK_POOLER_CONTAINER="$proj-pgbouncer-1" \
PG_SOAK_WORKERS="$proj-pipeshub-placement-1 $proj-pipeshub-fold-1" \
  bash -c 'cd ../../backend/python && PYTHONPATH="$PWD" exec "$0" "$@"' "$py" -m tests.integration.pg_fault.soak --hours "${SOAK_HOURS:-6}" --cycle-min "${CYCLE_MIN:-15}" --out "$out"
