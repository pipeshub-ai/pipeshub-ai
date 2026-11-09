#!/usr/bin/env bash
# Returns a Docker Compose install to MongoDB after scripts/migrate-to-postgres.sh.
#
#   scripts/rollback-to-mongo.sh [--reverse]
#
# Without --reverse it only succeeds when nothing was written to PostgreSQL since the copy (the verify finds both stores equal):
# MongoDB is then made the live store again with nothing lost. If the application has already written to PostgreSQL, the command
# refuses; run it again with --reverse to copy PostgreSQL back to MongoDB first (the application is stopped meanwhile).
# It removes docker-compose.postgres-docstore.yml from PIPESHUB_COMPOSE_OVERRIDES (in the env file) and starts the application.
# Run from deployment/docker-compose/; see scripts/import-login.sh for how the install is found.
set -euo pipefail
REVERSE=()
case "${1:-}" in "") ;; --reverse) REVERSE=(--reverse) ;; *) echo "usage: $0 [--reverse]" >&2; exit 2 ;; esac
# shellcheck source=import-login.sh
. "$(dirname "${BASH_SOURCE[0]}")/import-login.sh"
compose_setup
require_app
cleanup() { import_login_off || true; }
trap cleanup EXIT
trap 'exit 130' INT; trap 'exit 143' TERM HUP
APP_SERVICES=$(app_services)
# shellcheck disable=SC2086
[ -n "$APP_SERVICES" ] && "${DC[@]}" stop $APP_SERVICES
"${DC[@]}" up -d mongodb postgres pgbouncer
import_login_on
if ! "${DC[@]}" --profile postgres --profile postgres-migrate --profile docstore-migrate run --rm --no-deps docstore-migrate rollback "${REVERSE[@]}"; then
  echo "The rollback did not complete. The application is stopped and the document store is unchanged." >&2
  [ ${#REVERSE[@]} -eq 0 ] && echo "If the application had already written to PostgreSQL, run again with --reverse to copy those changes to MongoDB." >&2
  echo "To start the application again on its current store: ${DC[*]} up -d" >&2
  exit 1
fi
import_login_off   # before `up -d`: the migrate job's surface check fails while the import login can still log in
use_postgres_store off
"${DC[@]}" up -d
echo "The application is starting on MongoDB."
