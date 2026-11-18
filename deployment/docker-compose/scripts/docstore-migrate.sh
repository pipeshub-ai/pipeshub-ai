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

# A value as Compose reads it, without running the file as shell: 'single quotes' are literal, "double quotes" take \" and \\,
# and an unquoted value ends at a ` #` comment, with trailing blanks dropped.
env_get() {
  local v out="" c i=1
  v="$(sed -n "s/^$1=//p" "$ENV_FILE" | tail -1)"
  v="${v%$'\r'}"
  case "$v" in
    \'*) v="${v#\'}"; out="${v%%\'*}" ;;
    \"*)
      while [ "$i" -lt "${#v}" ]; do
        c="${v:$i:1}"
        if [ "$c" = "\\" ] && [ $((i + 1)) -lt "${#v}" ]; then i=$((i + 1)); out="$out${v:$i:1}"
        elif [ "$c" = '"' ]; then break
        else out="$out$c"; fi
        i=$((i + 1))
      done ;;
    *) out="${v%%[[:blank:]]#*}"; out="${out%"${out##*[![:blank:]]}"}" ;;
  esac
  printf '%s' "$out"
}
# Rewritten in place (cat into the same file): keeps its mode, owner and a symlink, and needs no GNU-only `sed -i`. The private
# temporary file sits beside it, and a failed sed leaves the env file as it was.
env_del() {
  local t rc=0
  t="$(mktemp "$ENV_FILE.XXXXXX")"
  { sed "/^$1=/d" "$ENV_FILE" >"$t" && cat "$t" >"$ENV_FILE"; } || rc=$?
  rm -f "$t"
  return "$rc"
}
# a missing final newline is not glued to the new line
env_set() { env_del "$1"; [ -z "$(tail -c1 "$ENV_FILE")" ] || echo >>"$ENV_FILE"; printf '%s=%s\n' "$1" "$2" >>"$ENV_FILE"; }

PROJECT="${PIPESHUB_PROJECT:-$(env_get COMPOSE_PROJECT_NAME)}"
DC=(docker compose --env-file "$ENV_FILE" -p "${PROJECT:-pipeshub-ai}" -f docker-compose.yml)
# guarded as in install.sh: bash before 4.4 (macOS ships 3.2) treats an empty array's "${a[@]}" as unset under set -u
_ov="${PIPESHUB_COMPOSE_OVERRIDES:-$(env_get PIPESHUB_COMPOSE_OVERRIDES)}"
if [ -n "$_ov" ]; then
  IFS=':' read -r -a _overrides <<<"$_ov"
  for _o in "${_overrides[@]}"; do [ -n "$_o" ] && DC+=(-f "$_o"); done
fi
PSQL=("${DC[@]}" exec -T postgres psql -q -v ON_ERROR_STOP=1 -U postgres -d pipeshub)

# An override as the bare name of a file in this directory, however it was written (./x.yml, /abs/path/x.yml, x.yml); a file of
# the same name elsewhere keeps its path, so it never passes for this directory's file.
override_name() {
  local d
  case "$1" in
    */*) d="$(cd "$(dirname "$1")" 2>/dev/null && pwd -P)" || d=""
         if [ "$d" = "$(pwd -P)" ]; then basename "$1"; else printf '%s' "$1"; fi ;;
    *) printf '%s' "$1" ;;
  esac
}
has_override() {
  local o
  [ -n "$_ov" ] || return 1
  IFS=':' read -r -a _overrides <<<"$_ov"
  for o in "${_overrides[@]}"; do [ "$(override_name "$o")" = "$1" ] && return 0; done
  return 1
}

# The tool, as the guide runs it: MongoDB's address comes from the application service's own environment, the schema job already
# built the tables. An empty import password reads as "login closed" to the tool. The service's own entrypoint starts the
# application whatever the command is; the image's entrypoint runs the command, as the unprivileged user the application runs as.
docstore() {
  # PostgreSQL's data volume, read-only, so preflight measures the space the copy will use. By its full name: a short name in
  # `run -v` is a new global volume, and one that does not exist yet must not be created here.
  local vol="${PROJECT:-pipeshub-ai}_postgres_data" mount=()
  docker volume inspect "$vol" >/dev/null 2>&1 && mount=(-v "$vol:/pgdata:ro" -e PG_DATA_DIR=/pgdata)
  PG_IMPORT_URL="postgresql://pipeshub_import:$(env_get PG_IMPORT_PASSWORD)@postgres:5432/pipeshub" \
  PG_MIGRATE_URL="postgresql://pipeshub_migrate:$(env_get PG_MIGRATE_PASSWORD)@postgres:5432/pipeshub" \
  PG_NODE_URL="postgresql://pipeshub_node_login:$(env_get PG_NODE_PASSWORD)@pgbouncer:6432/pipeshub" \
  MIGRATE_SCHEMA_COMMAND=none \
    "${DC[@]}" run --rm --no-deps -e PG_IMPORT_URL -e PG_MIGRATE_URL -e PG_NODE_URL -e MIGRATE_SCHEMA_COMMAND \
      ${mount[@]+"${mount[@]}"} --entrypoint /usr/local/bin/docker-entrypoint.sh pipeshub-ai node /app/backend/dist/scripts/docstore-migrate.js "$@"
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
