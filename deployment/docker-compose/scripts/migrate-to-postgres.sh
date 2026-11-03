#!/usr/bin/env bash
# Moves the application's documents from MongoDB to PostgreSQL on a Docker Compose install (offline cut).
#
#   scripts/migrate-to-postgres.sh [--dry-run] [--drain-wait SECONDS] [--force-drain] [--flip] | --reset
#
# Steps: (1) stop new chat turns and wait for the ones running (drain), (2) stop the application, (3) preflight, copy, verify
# in the one-shot migrator, (4) with --flip, write DOC_STORE=postgres to .env and start the application on PostgreSQL.
# --reset empties the target after a rolled-back move so the cut can be tried again (refused while MongoDB is retired).
# Without --flip it stops after a verified copy: you set DOC_STORE=postgres yourself (see postgres/README.md).
# Run from deployment/docker-compose/. The install needs COMPOSE_PROFILES to include `postgres,postgres-migrate` and the PG_* passwords
# in .env; MongoDB stays in place as the rollback source. Secrets are never printed.
set -euo pipefail

DRY=0; WAIT=300; FORCE=0; FLIP=0; RESET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --drain-wait) WAIT="$2"; shift ;;
    --force-drain) FORCE=1 ;;
    --flip) FLIP=1 ;;
    --reset) RESET=1 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

DC=(docker compose ${PIPESHUB_COMPOSE_FILES:-})
MIG=("${DC[@]}" --profile postgres --profile postgres-migrate --profile docstore-migrate run --rm --no-deps docstore-migrate)
APP_SERVICES=$("${DC[@]}" config --services | grep -Ev '^(mongodb|postgres|pgbouncer|pipeshub-migrate|pipeshub-roles|docstore-follow|docstore-reverse|pipeshub-placement|pipeshub-fold|pipeshub-maintain|docstore-migrate|redis|neo4j|arango|qdrant|etcd|zookeeper|kafka)$' || true)

echo "== 1/4 postgres is up and the schema is at head"
ENV_FILE="${PIPESHUB_ENV_FILE:-.env}"   # the file docker compose reads the install's settings from
# shellcheck source=import-login.sh
. "$(dirname "${BASH_SOURCE[0]}")/import-login.sh"
"${DC[@]}" up -d postgres pgbouncer mongodb
import_login_on
MOVED=0
cleanup() {   # any exit: the import login is shut again; a move that did not complete must not leave the application refusing chat turns
  rc=$?
  import_login_off
  if [ "$rc" -ne 0 ] && [ "$MOVED" = 0 ]; then "${MIG[@]}" drain --cancel >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

if [ "$RESET" = 1 ]; then
  MOVED=1   # nothing was drained, so there is nothing to cancel
  "${MIG[@]}" reset --yes
  exit 0
fi

echo "== 2/4 drain: no new chat turns, wait for the ones running (up to ${WAIT}s)"
if "${DC[@]}" ps --status running --services | grep -qx pipeshub-ai; then
  DRAIN_ARGS=(--wait "$WAIT"); [ "$FORCE" = 1 ] && DRAIN_ARGS+=(--force)
  "${DC[@]}" exec -T pipeshub-ai node /app/backend/dist/scripts/docstore-migrate.js drain "${DRAIN_ARGS[@]}" \
     || { echo "drain did not finish: runs are still in flight (use --force-drain to close them as interrupted)" >&2; "${MIG[@]}" drain --cancel >/dev/null; exit 1; }
else
  echo "   the application is not running; nothing to drain"
fi

echo "== 3/4 stop the application (MongoDB and PostgreSQL stay up)"
# shellcheck disable=SC2086
[ -n "$APP_SERVICES" ] && "${DC[@]}" stop $APP_SERVICES

echo "== 4/4 preflight, copy, verify"
ARGS=(migrate --offline); [ "$DRY" = 1 ] && ARGS+=(--dry-run)
if ! "${MIG[@]}" "${ARGS[@]}"; then
  echo "The move did not complete. MongoDB is unchanged and is still the store. Read the report above." >&2
  echo "Start the application again with: ${DC[*]} up -d" >&2
  exit 1
fi
import_login_off   # before any `up -d`: the migrate job's surface check fails while the import login can still log in
# the move is done: nothing from here on may touch the fences (a failing `up -d` must not un-retire MongoDB)
MOVED=1
if [ "$DRY" = 1 ]; then
  "${MIG[@]}" drain --cancel >/dev/null 2>&1 || true   # a dry run changes nothing: chat turns are accepted again
  echo "Dry run done. Start the application again with: ${DC[*]} up -d"
  exit 0
fi

if [ "$FLIP" = 1 ]; then
  grep -q '^DOC_STORE=' "$ENV_FILE" && sed -i 's/^DOC_STORE=.*/DOC_STORE=postgres/' "$ENV_FILE" || echo 'DOC_STORE=postgres' >> "$ENV_FILE"
  grep -q '^PG_NODE_URL=' "$ENV_FILE" || echo 'PG_NODE_URL=postgresql://pipeshub_node_login:${PG_NODE_PASSWORD}@pgbouncer:6432/pipeshub' >> "$ENV_FILE"
  grep -q '^PG_JOBS_URL=' "$ENV_FILE" || echo 'PG_JOBS_URL=postgresql://pipeshub_jobs:${PG_JOBS_PASSWORD}@pgbouncer:6432/pipeshub' >> "$ENV_FILE"
  "${DC[@]}" up -d
  echo "The application is starting on PostgreSQL. To go back: scripts/rollback-to-mongo.sh"
else
  echo "Verified. Set DOC_STORE=postgres (and PG_NODE_URL, PG_JOBS_URL) in .env and run: ${DC[*]} up -d"
fi
