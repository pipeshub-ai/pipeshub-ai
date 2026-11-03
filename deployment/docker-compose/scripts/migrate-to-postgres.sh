#!/usr/bin/env bash
# Moves the application's documents from MongoDB to PostgreSQL on a Docker Compose install (offline cut).
#
#   scripts/migrate-to-postgres.sh [--dry-run] [--drain-wait SECONDS] [--force-drain] [--flip] | --reset
#
# Steps: (1) stop new chat turns and wait for the ones running (drain), (2) stop the application, (3) preflight, copy, verify
# in the one-shot migrator, (4) with --flip, add docker-compose.postgres-docstore.yml to PIPESHUB_COMPOSE_OVERRIDES (in the env file) and start the application
# on PostgreSQL.
# --reset empties the target after a rolled-back move so the cut can be tried again (refused while MongoDB is retired).
# Without --flip it stops after a verified copy: you add that override yourself (see postgres/README.md).
# Run from deployment/docker-compose/; scripts/import-login.sh says how the install (project, env file, overrides) is found. It needs COMPOSE_PROFILES to include `postgres,postgres-migrate` and the PG_* passwords
# in .env; MongoDB stays in place as the rollback source. Secrets are never printed.
set -euo pipefail

DRY=0; WAIT=300; FORCE=0; FLIP=0; RESET=0
usage() { echo "usage: $0 [--dry-run] [--drain-wait SECONDS] [--force-drain] [--flip] | --reset" >&2; exit 2; }
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --drain-wait) { [ $# -ge 2 ] && [[ "$2" =~ ^[0-9]+$ ]]; } || usage; WAIT="$2"; shift ;;
    --force-drain) FORCE=1 ;;
    --flip) FLIP=1 ;;
    --reset) RESET=1 ;;
    *) usage ;;
  esac
  shift
done

# shellcheck source=import-login.sh
. "$(dirname "${BASH_SOURCE[0]}")/import-login.sh"
compose_setup
require_app
mig() { "${DC[@]}" --profile postgres --profile postgres-migrate --profile docstore-migrate run --rm --no-deps docstore-migrate "$@"; }
APP_SERVICES=$(app_services)

MOVED=0; DRAIN_PID=
cleanup() {   # any exit: the import login is shut again; a move that did not complete must not leave the application refusing chat turns
  rc=$?
  [ -z "$DRAIN_PID" ] || kill "$DRAIN_PID" 2>/dev/null || true
  import_login_off || true
  if [ "$rc" -ne 0 ] && [ "$MOVED" = 0 ]; then mig drain --cancel >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT   # before anything is opened or drained
trap 'exit 130' INT; trap 'exit 143' TERM HUP
echo "== 1/4 postgres is up and the schema is at head"
"${DC[@]}" up -d postgres pgbouncer mongodb
import_login_on

if [ "$RESET" = 1 ]; then
  MOVED=1   # nothing was drained, so there is nothing to cancel
  mig reset --yes
  exit 0
fi

echo "== 2/4 drain: no new chat turns, wait for the ones running (up to ${WAIT}s)"
if "${DC[@]}" ps --status running --services | grep -qx pipeshub-ai; then
  DRAIN_ARGS=(--wait "$WAIT"); [ "$FORCE" = 1 ] && DRAIN_ARGS+=(--force)
  # in the background and waited for: bash runs a signal's trap while it waits, not while a foreground command runs
  "${DC[@]}" exec -T pipeshub-ai node /app/backend/dist/scripts/docstore-migrate.js drain "${DRAIN_ARGS[@]}" & DRAIN_PID=$!
  wait "$DRAIN_PID" \
     || { echo "drain did not finish: runs are still in flight (use --force-drain to close them as interrupted)" >&2; mig drain --cancel >/dev/null; exit 1; }
else
  echo "   the application is not running; nothing to drain"
fi

echo "== 3/4 stop the application (MongoDB and PostgreSQL stay up)"
# shellcheck disable=SC2086
[ -n "$APP_SERVICES" ] && "${DC[@]}" stop $APP_SERVICES

echo "== 4/4 preflight, copy, verify"
ARGS=(migrate --offline); [ "$DRY" = 1 ] && ARGS+=(--dry-run)
if ! mig "${ARGS[@]}"; then
  echo "The move did not complete. MongoDB is unchanged and is still the store. Read the report above." >&2
  echo "Start the application again with: ${DC[*]} up -d" >&2
  exit 1
fi
# the move is done: nothing from here on may touch the fences (a failing `up -d` must not un-retire MongoDB)
MOVED=1
import_login_off   # before any `up -d`: the migrate job's surface check fails while the import login can still log in
if [ "$DRY" = 1 ]; then
  mig drain --cancel >/dev/null 2>&1 || true   # a dry run changes nothing: chat turns are accepted again
  echo "Dry run done. Start the application again with: ${DC[*]} up -d"
  exit 0
fi

if [ "$FLIP" = 1 ]; then
  use_postgres_store on   # the override sets DOC_STORE, PG_NODE_URL and PG_JOBS_URL and makes the application wait for the schema job
  "${DC[@]}" up -d
  echo "The application is starting on PostgreSQL. To go back: scripts/rollback-to-mongo.sh"
else
  echo "Verified. Add docker-compose.postgres-docstore.yml to PIPESHUB_COMPOSE_OVERRIDES and run: ${DC[*]} up -d"
fi
