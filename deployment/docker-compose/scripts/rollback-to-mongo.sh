#!/usr/bin/env bash
# Makes MongoDB the document store again after scripts/migrate-to-postgres.sh, with the migration tool's rollback.
#
#   scripts/rollback-to-mongo.sh             offline move, before the application wrote to PostgreSQL: checks MongoDB still
#                                            matches and makes it the store
#   scripts/rollback-to-mongo.sh --reverse   offline move, after it did: copies every document from PostgreSQL back over MongoDB
#   scripts/rollback-to-mongo.sh --online    online move with `reverse` running since the switch: writes pause while the last
#                                            changes go back; the application keeps running
#
# Steps: open the import login, stop the application (not with --online), run the rollback, close the import login. Then start the
# application on MongoDB: without docker-compose.postgres-docstore.yml, and with it removed from PIPESHUB_COMPOSE_OVERRIDES. It
# exits like the tool: 0 done, 1 refused (the store is unchanged), 2 error. scripts/docstore-migrate.sh says how the install is
# found; no password is printed.
set -euo pipefail

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }
FLAG=()
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --reverse|--online)
      [ ${#FLAG[@]} -eq 0 ] || { echo "choose --reverse or --online, not both" >&2; exit 2; }
      FLAG=("$arg") ;;
    *) echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

# shellcheck source-path=SCRIPTDIR source=docstore-migrate.sh
. "$(dirname "${BASH_SOURCE[0]}")/docstore-migrate.sh"

trap import_login_close EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
import_login_open
[ "${FLAG[*]}" = --online ] || "${DC[@]}" stop pipeshub-ai
docstore rollback "${FLAG[@]}"
echo "MongoDB is the store again. Remove docker-compose.postgres-docstore.yml from PIPESHUB_COMPOSE_OVERRIDES in the env file and start the application:" >&2
dc="${DC[*]}"
echo "  ${dc/ -f docker-compose.postgres-docstore.yml/} up -d pipeshub-ai" >&2
