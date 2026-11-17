#!/usr/bin/env bash
# Moves a Docker Compose install's document store from MongoDB to PostgreSQL with the migration tool.
#
#   scripts/migrate-to-postgres.sh --offline       open the import login, stop the application, preflight, copy and verify
#                                                  (migrate --offline), close the import login
#   scripts/migrate-to-postgres.sh --online        open the import login, preflight, copy while the application runs
#                                                  (migrate --online), then follow MongoDB until Ctrl+C (or a signal to its
#                                                  process group). The login stays open: cutover, reverse and rollback
#                                                  --online write as it
#   scripts/migrate-to-postgres.sh --close-login   close the import login (after an online move, or when `status` warns)
#   scripts/migrate-to-postgres.sh --reset         empty the copied tables after a failed copy, so it can run again
#
# Before it: the `postgres` and `postgres-migrate` profiles in COMPOSE_PROFILES, the PG_*_PASSWORD values in the env file, and
# `docker compose up -d postgres pgbouncer pipeshub-migrate` done (the schema job exited 0). It exits like the tool: 0 done,
# 1 refused or found problems, 2 error. Re-running after a failed or interrupted run is safe. scripts/docstore-migrate.sh says how
# the install is found; no password is printed.
set -euo pipefail

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }
MODE=""
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --offline|--online|--close-login|--reset)
      [ -z "$MODE" ] || { echo "choose one of --offline, --online, --close-login, --reset" >&2; exit 2; }
      MODE="$arg" ;;
    *) echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done
[ -n "$MODE" ] || { usage >&2; exit 2; }

# shellcheck source-path=SCRIPTDIR source=docstore-migrate.sh
. "$(dirname "${BASH_SOURCE[0]}")/docstore-migrate.sh"

if [ "$MODE" = --close-login ]; then import_login_close; exit; fi

KEEP_LOGIN=0
# any exit closes the login, unless an online move is running that still writes as it
trap '[ "$KEEP_LOGIN" = 1 ] || import_login_close' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
import_login_open

case "$MODE" in
  --reset)
    docstore reset --yes ;;
  --offline)
    "${DC[@]}" stop pipeshub-ai
    docstore preflight
    docstore migrate --offline
    echo "Copied and verified. Start the application on PostgreSQL:" >&2
    echo "  ${DC[*]} -f docker-compose.postgres-docstore.yml up -d pipeshub-ai" >&2
    echo "and add docker-compose.postgres-docstore.yml to PIPESHUB_COMPOSE_OVERRIDES in the env file to keep it." >&2 ;;
  --online)
    docstore preflight --allow-clients
    docstore migrate --online
    KEEP_LOGIN=1
    echo "Following MongoDB; stop with Ctrl+C. Run cutover and reverse with scripts/docstore-migrate.sh while the import login is open." >&2
    # A handler, not '' (an ignored signal stays ignored in docker, which then never stops the follower): docker gets Ctrl+C and
    # passes it on, the tool stops and exits, and this shell goes on to the message.
    trap 'true' INT TERM
    rc=0
    docstore follow run || rc=$?
    echo "The follower stopped. The import login stays open while the move runs; close it when the move is over: $0 --close-login" >&2
    exit "$rc" ;;
esac
