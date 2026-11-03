#!/usr/bin/env bash
# Returns a Docker Compose install to MongoDB after scripts/migrate-to-postgres.sh.
#
#   scripts/rollback-to-mongo.sh [--reverse]
#
# Without --reverse it only succeeds when nothing was written to PostgreSQL since the copy (the verify finds both stores equal):
# MongoDB is then made the live store again with nothing lost. If the application has already written to PostgreSQL, the command
# refuses; run it again with --reverse to copy PostgreSQL back to MongoDB first (the application is stopped meanwhile).
# Run from deployment/docker-compose/.
set -euo pipefail
REVERSE=()
[ "${1:-}" = "--reverse" ] && REVERSE=(--reverse)
DC=(docker compose ${PIPESHUB_COMPOSE_FILES:-})
ENV_FILE="${PIPESHUB_ENV_FILE:-.env}"   # the file docker compose reads the install's settings from
# shellcheck source=import-login.sh
. "$(dirname "${BASH_SOURCE[0]}")/import-login.sh"
APP_SERVICES=$("${DC[@]}" config --services | grep -Ev '^(mongodb|postgres|pgbouncer|pipeshub-migrate|pipeshub-roles|docstore-follow|docstore-reverse|pipeshub-placement|pipeshub-fold|pipeshub-maintain|docstore-migrate|redis|neo4j|arango|qdrant|etcd|zookeeper|kafka)$' || true)
# shellcheck disable=SC2086
[ -n "$APP_SERVICES" ] && "${DC[@]}" stop $APP_SERVICES
"${DC[@]}" up -d mongodb postgres pgbouncer
import_login_on
trap import_login_off EXIT
"${DC[@]}" --profile postgres --profile postgres-migrate --profile docstore-migrate run --rm --no-deps docstore-migrate rollback "${REVERSE[@]}"
import_login_off   # before `up -d`: the migrate job's surface check fails while the import login can still log in
touch "$ENV_FILE"
grep -q '^DOC_STORE=' "$ENV_FILE" && sed -i 's/^DOC_STORE=.*/DOC_STORE=mongo/' "$ENV_FILE" || echo 'DOC_STORE=mongo' >> "$ENV_FILE"
"${DC[@]}" up -d
echo "The application is starting on MongoDB."
