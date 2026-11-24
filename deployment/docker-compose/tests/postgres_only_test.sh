#!/usr/bin/env bash
# docker-compose.postgres-only.yml starts PipesHub on PostgreSQL alone: with every profile switched on (COMPOSE_PROFILES='*'), no
# MongoDB, Qdrant, Redis, Neo4j, ArangoDB, etcd, Zookeeper or Kafka service renders or runs; the app selects PostgreSQL for the
# document store, graph, vectors, key-value store, broker and scheduler; postgres, pgbouncer, the migration job and the placement
# and fold workers start without their profiles (with every profile on, and with none), and each worker does its job. The app is stood in for by a sleeping container of the postgres image (building
# the application image is out of scope here); its dependencies and environment are the overlay's own. The optional helpers that are no
# store (sandbox, web search, page reading and the opt-in PostgreSQL metrics exporters) are left out of the run so that their images are not pulled.
# Test-only environment: PROJECT_PREFIX names the compose project; EXTRA_COMPOSE_FILE adds one compose file. Both change nothing when
# unset. Needs docker; runs on PostgreSQL 17 and 18 (PG_MAJORS to choose); PG_IMAGE_<major> names the built postgres images (default
# pmcompose/postgres:<major>-pmc) and MIGRATE_IMAGE the migrate image (default: built here from backend/db/Dockerfile as pmcompose/migrate:pmc).
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
for major in ${PG_MAJORS:-17 18}; do
image_var="PG_IMAGE_$major"; image="${!image_var:-pmcompose/postgres:$major-pmc}"
proj="${PROJECT_PREFIX:-pgimg}-only-$major-$$"

{ for v in ADMIN NODE CONNECTORS INDEXING QUERY JOBS MIGRATE BACKUP KV; do echo "PG_${v}_PASSWORD=$(pw)"; done
  echo "PG_MAJOR=$major"; echo "PG_IMAGE=${image%:*}"; echo "PG_IMAGE_REV=${image##*:$major-}"
  echo "PIPESHUB_MIGRATE_IMAGE=${migrate_image%:*}"; echo "IMAGE_TAG=${migrate_image##*:}"
  # Every profile on: the overlay must keep the other stores out regardless (a profile of their own would be switched on too).
  echo "COMPOSE_PROFILES=*"; } > "$env_file"
{ echo "services:"; echo "  pipeshub-ai:"
  echo "    image: $image"; echo "    entrypoint: [\"sleep\", \"infinity\"]"; echo "    restart: \"no\""
  echo "    healthcheck: {disable: true}"; echo "    ports: !reset []"; echo "    volumes: !reset []"; echo "    build: !reset null"
  echo "    networks: !override [pipeshub]"
  echo "    depends_on: {sandbox-image: !reset null, docker-socket-proxy: !reset null}"
  for h in sandbox-image docker-socket-proxy web-readers-gateway searxng crawl4ai postgres-exporter pgbouncer-exporter; do echo "  $h: !reset null"; done; } > "$work/stub-app.yml"

services="$(dc config --services)"
echo "PostgreSQL $major"
check "no other store renders" "$(echo "$services" | grep -cE "$stores" || true)" 0
# With no profile on at all (the documented start), the PostgreSQL stack renders too; '*' alone cannot show that.
bare="$(COMPOSE_PROFILES= dc config --services)"
for s in postgres pgbouncer pipeshub-migrate pipeshub-placement pipeshub-fold pipeshub-ai; do
  check "$s renders with every profile on" "$(echo "$services" | grep -cx "$s")" 1
  check "$s renders with no profile on" "$(echo "$bare" | grep -cx "$s")" 1
done
check "the metrics exporters start only with their own profile" "$(echo "$bare" | grep -cE '^(postgres|pgbouncer)-exporter$' || true)" 0
envs="$(dc config --format json | python3 -c 'import json,sys; e=json.load(sys.stdin)["services"]["pipeshub-ai"]["environment"]; print(" ".join(k+"="+str(e.get(k)) for k in ("DOC_STORE","DATA_STORE","VECTOR_DB_TYPE","KV_STORE_TYPE","MESSAGE_BROKER","REDIS_MODE","CRAWL_SCHEDULER")))')"
check "the app selects PostgreSQL for every store" "$envs" \
  "DOC_STORE=postgres DATA_STORE=postgres VECTOR_DB_TYPE=pgvector KV_STORE_TYPE=postgres MESSAGE_BROKER=postgres REDIS_MODE=postgres CRAWL_SCHEDULER=postgres"

if ! dc up -d --no-build > "$work/up.log" 2>&1; then echo "FAIL $major: compose up failed:"; cat "$work/up.log"; exit 1; fi
check "the migration job exits 0" "$($DOCKER inspect -f '{{.State.ExitCode}}' "$proj-pipeshub-migrate-1")" 0
# Bounded wait for each worker's banner (printed once it is connected and looping), not a fixed sleep.
until_log() { for _ in $(seq 60); do [ "$(dc logs "$1" 2>&1 | grep -c "$2")" -ge 1 ] && break; sleep 2; done; dc logs "$1" 2>&1 | grep -c "$2"; }
check "the placement worker is watching" "$(until_log pipeshub-placement 'placement worker running')" 1
check "the fold worker is watching" "$(until_log pipeshub-fold 'fold worker running')" 1
running="$($DOCKER ps --filter "label=com.docker.compose.project=$proj" --format '{{.Label "com.docker.compose.service"}}' | sort | tr '\n' ' ')"
check "exactly the PostgreSQL stack runs" "$running" "pgbouncer pipeshub-ai pipeshub-fold pipeshub-placement postgres "
check "no container of another store exists" "$($DOCKER ps -a --filter "label=com.docker.compose.project=$proj" --format '{{.Label "com.docker.compose.service"}}' | grep -cE "$stores" || true)" 0
for w in placement fold; do
  check "the $w worker has not restarted" "$($DOCKER inspect -f '{{.RestartCount}}' "$proj-pipeshub-$w-1")" 0
done
# A banner is printed before any database work, and both loops outlive a failed pass: check what each worker did.
org=0123456789abcdef01234567
sql() { dc exec -T postgres psql -qtAX -U postgres -d pipeshub -v ON_ERROR_STOP=1 -c "$1"; }
until_sql() { for _ in $(seq 60); do [ "$(sql "$1")" = "$2" ] && break; sleep 2; done; sql "$1"; }
sql "INSERT INTO retrieval.placement_requests (org_id) VALUES ('$org')" >/dev/null
check "the placement worker created the org's partition" \
  "$(until_sql "SELECT to_regclass('retrieval.points_o_$org') IS NOT NULL" t)" t
sql "INSERT INTO retrieval.lexical_df_delta (org_id, collection_id, space_id, term, d) VALUES ('$org', 1, 1, -1, 3)" >/dev/null
check "the fold worker folded a point-count delta" \
  "$(until_sql "SELECT n_points FROM retrieval.collection_stats WHERE org_id = '$org'" 3)" 3
dc down -v >/dev/null 2>&1
done

# Without a password the overlay does not render at all (no app with an empty login).
grep -v '^PG_KV_PASSWORD=' "$work/env" > "$work/env-nokv"; env_file="$work/env-nokv"
out="$(dc config 2>&1 || true)"
check "an empty PG_KV_PASSWORD stops the overlay from rendering" "$(echo "$out" | grep -q 'PG_KV_PASSWORD is empty or unset' && echo refused)" refused
proj=""
exit $fail
