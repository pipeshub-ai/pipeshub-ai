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
FLAG=""   # a string, not an array: bash before 4.4 treats an empty array as unset under set -u
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --reverse|--online)
      [ -z "$FLAG" ] || { echo "choose --reverse or --online, not both" >&2; exit 2; }
      FLAG="$arg" ;;
    *) echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

# shellcheck source-path=SCRIPTDIR source=docstore-migrate.sh
. "$(dirname "${BASH_SOURCE[0]}")/docstore-migrate.sh"

# An online rollback that does not complete may leave either store live (a handover stuck partway can have fenced MongoDB while
# PostgreSQL stays read-only), with the reverse follower still writing as the import login: it keeps that login and its password
# then. Every other ending closes it.
DONE=0
trap '[ "$FLAG" = --online ] && [ "$DONE" = 0 ] || import_login_close' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
import_login_open
[ "$FLAG" = --online ] || "${DC[@]}" stop pipeshub-ai
rc=0
docstore rollback ${FLAG:+"$FLAG"} || rc=$?
if [ "$rc" != 0 ]; then
  # the tool's status is kept: 1 refused, 2 error
  [ "$FLAG" = --online ] && echo "The online rollback did not complete. Follow the tool's report above to see which store is live before you close the import login or restart the application. The import login stays open for the reverse follower; close it when the move is over: scripts/migrate-to-postgres.sh --close-login" >&2
  exit "$rc"
fi
DONE=1
echo "MongoDB is the store again. Remove docker-compose.postgres-docstore.yml from PIPESHUB_COMPOSE_OVERRIDES in the env file and start the application:" >&2
# the command without the PostgreSQL override, however it was written
cmd=(); skip=0
for a in "${DC[@]}"; do
  if [ "$skip" = 1 ]; then
    skip=0
    [ "$(override_name "$a")" = docker-compose.postgres-docstore.yml ] && { unset 'cmd[${#cmd[@]}-1]'; continue; }
  fi
  [ "$a" = -f ] && skip=1
  cmd+=("$a")
done
printf ' ' >&2; printf ' %q' "${cmd[@]}" up -d pipeshub-ai >&2; echo >&2
