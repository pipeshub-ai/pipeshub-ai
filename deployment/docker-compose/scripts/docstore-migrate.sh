#!/usr/bin/env bash
# Runs the document-store migration tool of a Docker Compose install in a one-off container of the PipesHub image, with the
# PostgreSQL logins built from the install's env file:
#
#   scripts/docstore-migrate.sh status
#   scripts/docstore-migrate.sh cutover --dry-run --fence-armed --shadow-clean
#
# It does not open or close the import login (pipeshub_import): migrate-to-postgres.sh and rollback-to-mongo.sh do, and source
# this file for the helpers below. Commands that write to PostgreSQL (follow, verify, cutover, reverse, reset, rollback) need the
# login open.
#
# The install is found the way install.sh finds it: docker-compose.yml in deployment/docker-compose, the env file .env there
# (PIPESHUB_ENV_FILE), the project PIPESHUB_PROJECT, else COMPOSE_PROJECT_NAME in the env file, else pipeshub-ai, plus the files in
# PIPESHUB_COMPOSE_OVERRIDES (colon-separated, from the environment or the env file). Passwords are read from the env file and
# reach containers through the environment, never a command line, and are never printed.
set -euo pipefail

ENV_FILE="${PIPESHUB_ENV_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env}"
case "$ENV_FILE" in /*) ;; *) ENV_FILE="$PWD/$ENV_FILE" ;; esac
cd "$(dirname "${BASH_SOURCE[0]}")/.."

die() { echo "$*" >&2; exit 1; }
[ -f "$ENV_FILE" ] || die "no env file at $ENV_FILE: run from a PipesHub Docker Compose install, or set PIPESHUB_ENV_FILE"

env_get() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1 | tr -d '\r"'; }
# sed -i keeps the file's mode; a missing final newline is not glued to the new line
env_del() { sed -i "/^$1=/d" "$ENV_FILE"; }
env_set() { env_del "$1"; [ -z "$(tail -c1 "$ENV_FILE")" ] || echo >>"$ENV_FILE"; printf '%s=%s\n' "$1" "$2" >>"$ENV_FILE"; }

PROJECT="${PIPESHUB_PROJECT:-$(env_get COMPOSE_PROJECT_NAME)}"
DC=(docker compose --env-file "$ENV_FILE" -p "${PROJECT:-pipeshub-ai}" -f docker-compose.yml)
IFS=':' read -r -a _overrides <<<"${PIPESHUB_COMPOSE_OVERRIDES:-$(env_get PIPESHUB_COMPOSE_OVERRIDES)}"
for _o in "${_overrides[@]}"; do [ -n "$_o" ] && DC+=(-f "$_o"); done
PSQL=("${DC[@]}" exec -T postgres psql -q -v ON_ERROR_STOP=1 -U postgres -d pipeshub)

# The tool, as the guide runs it: MongoDB's address comes from the application service's own environment, the schema job already
# built the tables. An empty import password reads as "login closed" to the tool.
docstore() {
  PG_IMPORT_URL="postgresql://pipeshub_import:$(env_get PG_IMPORT_PASSWORD)@postgres:5432/pipeshub" \
  PG_MIGRATE_URL="postgresql://pipeshub_migrate:$(env_get PG_MIGRATE_PASSWORD)@postgres:5432/pipeshub" \
  PG_NODE_URL="postgresql://pipeshub_node_login:$(env_get PG_NODE_PASSWORD)@pgbouncer:6432/pipeshub" \
  MIGRATE_SCHEMA_COMMAND=none \
    "${DC[@]}" run --rm --no-deps -e PG_IMPORT_URL -e PG_MIGRATE_URL -e PG_NODE_URL -e MIGRATE_SCHEMA_COMMAND \
      pipeshub-ai node /app/backend/dist/scripts/docstore-migrate.js "$@"
}

# Lets pipeshub_import log in. The password is kept in the env file while the login is open, so a re-run after an interrupted one
# and docstore-migrate.sh in another terminal use the same one; the server is sent only its SCRAM verifier.
import_login_open() {
  local pw verifier
  pw="$(env_get PG_IMPORT_PASSWORD)"
  if [ -z "$pw" ]; then
    pw="$(set +o pipefail; LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 48)"
    env_set PG_IMPORT_PASSWORD "$pw"
  fi
  verifier="$(PG_IMPORT_PASSWORD="$pw" "${DC[@]}" exec -T -e PG_IMPORT_PASSWORD postgres scram-verifier.pl PG_IMPORT_PASSWORD)"
  printf "ALTER ROLE pipeshub_import LOGIN PASSWORD '%s'\n" "$verifier" | "${PSQL[@]}" >/dev/null
}

# Shuts the login and drops its password from the env file; the next move makes a new one.
import_login_close() {
  if printf 'ALTER ROLE pipeshub_import NOLOGIN\n' | "${PSQL[@]}" >/dev/null; then
    env_del PG_IMPORT_PASSWORD
  else
    echo "WARNING: the import login could not be closed. Close it with: ${DC[*]} exec postgres psql -U postgres -d pipeshub -c 'ALTER ROLE pipeshub_import NOLOGIN'" >&2
    return 1
  fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  [ $# -gt 0 ] || { echo "usage: $0 <command> [flags]   (commands: $0 help)" >&2; exit 2; }
  docstore "$@"
fi
