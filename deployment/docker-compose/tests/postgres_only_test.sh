#!/usr/bin/env bash
# docker-compose.postgres-only.yml starts PipesHub on PostgreSQL alone: with every store profile switched on in COMPOSE_PROFILES, no
# MongoDB, Qdrant, Redis, Neo4j, ArangoDB, etcd, Zookeeper or Kafka service renders or runs; the app selects PostgreSQL for the
# document store, graph, vectors, key-value store, broker and scheduler; postgres, pgbouncer, the migration job and the placement
# and fold workers start without their profiles. The app is stood in for by a sleeping container of the postgres image (building
# the application image is out of scope here); its dependencies and environment are the overlay's own.
# Test-only environment: PROJECT_PREFIX names the compose project; EXTRA_COMPOSE_FILE adds one compose file. Both change nothing when
# unset. Needs docker; PG_IMAGE_18 names the built postgres image (default pmcompose/postgres:18-pmc) and MIGRATE_IMAGE the migrate
# image (default: built here from backend/db/Dockerfile as pmcompose/migrate:pmc).
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="${DOCKER:-docker}"
work="$(mktemp -d)"; proj=""
env_file="$work/env"
dc() { $DOCKER compose --env-file "$env_file" -p "$proj" -f docker-compose.yml -f docker-compose.postgres-only.yml ${EXTRA_COMPOSE_FILE:+-f "$EXTRA_COMPOSE_FILE"} -f "$work/stub-app.yml" "$@"; }
cleanup() { [ -n "$proj" ] && dc down -v >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT
pw() { head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
stores='^(mongodb|qdrant|redis|neo4j|arango|etcd|zookeeper|kafka-1)$'
migrate_image="${MIGRATE_IMAGE:-pmcompose/migrate:pmc}"
if [ -z "${MIGRATE_IMAGE:-}" ]; then $DOCKER build -q -f ../../backend/db/Dockerfile -t "$migrate_image" ../.. >/dev/null; fi
image="${PG_IMAGE_18:-pmcompose/postgres:18-pmc}"
proj="${PROJECT_PREFIX:-pgimg}-only-$$"

{ for v in ADMIN NODE CONNECTORS INDEXING QUERY JOBS MIGRATE BACKUP KV; do echo "PG_${v}_PASSWORD=$(pw)"; done
  echo "PG_MAJOR=18"; echo "PG_IMAGE=${image%:*}"; echo "PG_IMAGE_REV=${image##*:18-}"
  echo "PIPESHUB_MIGRATE_IMAGE=${migrate_image%:*}"; echo "IMAGE_TAG=${migrate_image##*:}"
  # Every store profile on: the overlay must keep their services off regardless.
  echo "COMPOSE_PROFILES=graph-neo4j,graph-arango,kv-etcd,broker-kafka"; } > "$env_file"
{ echo "services:"; echo "  pipeshub-ai:"
  echo "    image: $image"; echo "    entrypoint: [\"sleep\", \"infinity\"]"; echo "    restart: \"no\""
  echo "    healthcheck: {disable: true}"; echo "    ports: !reset []"; echo "    volumes: !reset []"; echo "    build: !reset null"
  echo "    networks: !override [pipeshub]"; } > "$work/stub-app.yml"

services="$(dc config --services)"
check "no other store renders" "$(echo "$services" | grep -cE "$stores" || true)" 0
for s in postgres pgbouncer pipeshub-migrate pipeshub-placement pipeshub-fold pipeshub-ai; do
  check "$s renders without its profile" "$(echo "$services" | grep -cx "$s")" 1
done
envs="$(dc config --format json | python3 -c 'import json,sys; e=json.load(sys.stdin)["services"]["pipeshub-ai"]["environment"]; print(" ".join(k+"="+str(e.get(k)) for k in ("DOC_STORE","DATA_STORE","VECTOR_DB_TYPE","KV_STORE_TYPE","MESSAGE_BROKER","REDIS_MODE","CRAWL_SCHEDULER")))')"
check "the app selects PostgreSQL for every store" "$envs" \
  "DOC_STORE=postgres DATA_STORE=postgres VECTOR_DB_TYPE=pgvector KV_STORE_TYPE=postgres MESSAGE_BROKER=postgres REDIS_MODE=postgres CRAWL_SCHEDULER=postgres"

if ! dc up -d --no-build > "$work/up.log" 2>&1; then echo "FAIL compose up failed:"; cat "$work/up.log"; exit 1; fi
check "the migration job exits 0" "$($DOCKER inspect -f '{{.State.ExitCode}}' "$proj-pipeshub-migrate-1")" 0
sleep 5    # long enough for a worker that cannot connect to exit and be restarted
running="$($DOCKER ps --filter "label=com.docker.compose.project=$proj" --format '{{.Label "com.docker.compose.service"}}' | sort | tr '\n' ' ')"
check "exactly the PostgreSQL stack runs" "$running" "pgbouncer pipeshub-ai pipeshub-fold pipeshub-placement postgres "
check "no container of another store exists" "$($DOCKER ps -a --filter "label=com.docker.compose.project=$proj" --format '{{.Label "com.docker.compose.service"}}' | grep -cE "$stores" || true)" 0
for w in placement fold; do
  check "the $w worker has not restarted" "$($DOCKER inspect -f '{{.RestartCount}}' "$proj-pipeshub-$w-1")" 0
done
check "the placement worker is watching" "$(dc logs pipeshub-placement 2>&1 | grep -c 'placement worker running')" 1
check "the fold worker is watching" "$(dc logs pipeshub-fold 2>&1 | grep -c 'fold worker running')" 1
dc down -v >/dev/null 2>&1

# Without a password the overlay does not render at all (no app with an empty login).
grep -v '^PG_KV_PASSWORD=' "$work/env" > "$work/env-nokv"; env_file="$work/env-nokv"
out="$(dc config 2>&1 || true)"
check "an empty PG_KV_PASSWORD stops the overlay from rendering" "$(echo "$out" | grep -q 'PG_KV_PASSWORD is empty or unset' && echo refused)" refused
proj=""
exit $fail
