#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE_DIR="$ROOT_DIR/deployment/docker-compose"
# env.template ships SECRET_KEY and DOCKER_PROXY_TOKEN empty (SEC-D-05, ADR-KEY-02 D6), which the build files refuse
# to render; any random values do here.
ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
{ cat "$COMPOSE_DIR/env.template"; printf 'SECRET_KEY=%s\nDOCKER_PROXY_TOKEN=%s\n' "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" \
  "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  # SEC-D-06: env.template ships the store credentials empty, which Compose refuses to render.
  printf 'REDIS_PASSWORD=ci\nQDRANT_API_KEY=ci\nMONGO_PASSWORD=ci\nKAFKA_PASSWORD=ci\nNEO4J_PASSWORD=ci-password\n'; } > "$ENV_FILE"

# Presets that run the base file's single-node replica set, the CI
# integration stacks included: they test the shipped topology.
REPLSET_COMPOSE_FILES=(
  docker-compose.yml
  docker-compose.build.neo4j.yml
  docker-compose.prod-legacy.yml
  docker-compose.integration.arango.yml
  docker-compose.integration.neo4j.yml
)

render_compose() {
  local compose_file="$1"
  local output_file="$2"

  docker compose \
    -f "$COMPOSE_DIR/$compose_file" \
    --env-file "$ENV_FILE" \
    config --format json > "$output_file"
}

assert_mongo_config() {
  local rendered_file="$1"
  local compose_file="$2"
  local expected_image="$3"
  local expected_cache_gb="$4"
  local expected_tunables="$5"
  local expected_memory_bytes="${6:-}"

  python3 - "$rendered_file" "$compose_file" "$expected_image" "$expected_cache_gb" "$expected_tunables" "$expected_memory_bytes" <<'PY'
import json
import sys

rendered_file, compose_file, expected_image, expected_cache_gb, expected_tunables, expected_memory_bytes = sys.argv[1:]

with open(rendered_file, "r", encoding="utf-8") as fh:
    data = json.load(fh)

mongo = data["services"]["mongodb"]
command = mongo.get("command") or []
environment = mongo.get("environment") or {}

expected_command = [
    "mongod", "--bind_ip_all", "--replSet", "rs0", "--oplogSize", "1024",
    "--keyFile", "/mongodb-keyfile/keyfile", "--wiredTigerCacheSizeGB", expected_cache_gb,
]

checks = [
    (mongo.get("image") == expected_image, f"{compose_file}: expected image {expected_image}, got {mongo.get('image')!r}"),
    (command == expected_command, f"{compose_file}: unexpected MongoDB command {command!r}"),
    (environment.get("GLIBC_TUNABLES", "") == expected_tunables, f"{compose_file}: expected GLIBC_TUNABLES={expected_tunables!r}, got {environment.get('GLIBC_TUNABLES')!r}"),
]

if expected_memory_bytes:
    memory = str(mongo.get("deploy", {}).get("resources", {}).get("limits", {}).get("memory", ""))
    checks.append((memory == expected_memory_bytes, f"{compose_file}: expected memory limit {expected_memory_bytes}, got {memory!r}"))

for passed, message in checks:
    if not passed:
        raise SystemExit(f"FAIL: {message}")
PY
}

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir" "$ENV_FILE"' EXIT

for compose_file in "${REPLSET_COMPOSE_FILES[@]}"; do
  rendered="$tmp_dir/${compose_file}.default.json"
  render_compose "$compose_file" "$rendered"
  assert_mongo_config "$rendered" "$compose_file" "mongo:8.0.17" "1" "glibc.pthread.rseq=0"
done

override_rendered="$tmp_dir/docker-compose.override.json"
MONGO_IMAGE_TAG=7.0 \
MONGO_CACHE_GB=2 \
MONGO_GLIBC_TUNABLES=glibc.pthread.rseq=1 \
MONGO_MEMORY_LIMIT=3G \
  render_compose docker-compose.yml "$override_rendered"

assert_mongo_config "$override_rendered" "docker-compose.yml" "mongo:7.0" "2" "glibc.pthread.rseq=1" "3221225472"

printf 'MongoDB compose stability tests passed.\n'
