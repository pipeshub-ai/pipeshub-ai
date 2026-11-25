#!/usr/bin/env bash
# Restores the PostgreSQL profile's database from the pgBackRest repository of docker-compose.postgres-backup.yml.
#
#   scripts/pg-restore.sh <time|lsn:L|latest> [--into <volume>] [--set <label>]   restore into a NEW volume, started as a separate server
#   scripts/pg-restore.sh <time|lsn:L|latest> --in-place [--set <label>]          restore over the stack's own data volume (stack stopped)
#
# <time> is a PostgreSQL timestamp with a time zone, for example "2026-11-15 09:30:00+00": the restore keeps every transaction committed
# up to it and nothing after (recovery_target_time, https://www.postgresql.org/docs/17/runtime-config-wal.html 19.5.6). `latest` replays
# every archived WAL segment. `lsn:<location>` stops just after the first WAL record at or after that location (recovery_target_lsn,
# inclusive): with the location read before a restore point is made on a quiet database, that is exactly the quiet state, and unlike a
# time it needs no later commit to stop at. pgBackRest picks the backup that can reach the time or location ("a backup that contains the
# requested time or lsn", https://pgbackrest.org/user-guide.html).
# Run it from deployment/docker-compose with the compose files, project and profiles of the stack, given as COMPOSE_FILE,
# COMPOSE_PROJECT_NAME and COMPOSE_PROFILES (environment or .env), for example
#   COMPOSE_FILE=docker-compose.yml:docker-compose.postgres-docstore.yml:docker-compose.postgres-backup.yml COMPOSE_PROJECT_NAME=pipeshub-ai \
#   COMPOSE_PROFILES=graph-neo4j,sandbox,postgres,postgres-migrate scripts/pg-restore.sh latest
# (the document-store file needs both `postgres` and `postgres-migrate`). It restores PostgreSQL only: Neo4j, Qdrant and Redis come back
# from their own copy.
#
# A new-volume restore leaves the stack untouched: the copy runs as container <volume>, does not archive (so it never writes to the
# repository), and listens on its own socket; read it with `docker exec -it <volume> psql -h /tmp -U postgres -d pipeshub`. Remove it with
# `docker rm -f <volume> && docker volume rm <volume>`. To put the stack itself back to that time, stop the stack and use --in-place.
set -euo pipefail
cd "$(dirname "$0")/.."
# every compose call through scripts/pipeshub-compose.sh: the stack's files also when install.sh manages it (PIPESHUB_COMPOSE_OVERRIDES)
compose() { scripts/pipeshub-compose.sh "$@"; }

usage() { sed -n '4,5p' "$0" >&2; exit 2; }
[ $# -ge 1 ] || usage
target="$1"; shift
into=""; in_place=0; set_label=""
while [ $# -gt 0 ]; do
  case "$1" in
    --into) [ $# -ge 2 ] || usage; into="$2"; shift 2 ;;
    --in-place) in_place=1; shift ;;
    # a backup other than pgBackRest's choice: after a WAL gap, `latest --set <newest backup before the gap>` restores up to the gap
    # ("--set: The backup set to be restored", https://pgbackrest.org/command.html)
    --set) [ $# -ge 2 ] || usage; set_label="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$target" ] || usage
[ "$in_place" = 1 ] && [ -n "$into" ] && { echo "pg-restore: --in-place and --into exclude each other" >&2; exit 2; }

# read whole outputs first: with pipefail, a reader that stops early (grep -q, head) fails the pipeline when compose gets SIGPIPE
config="$(compose config)" || {
  echo "pg-restore: Compose rejected the project (above): COMPOSE_FILE, COMPOSE_PROJECT_NAME and COMPOSE_PROFILES must be the stack's own" >&2; exit 2; }
services="$(compose config --services)"
project="$(sed -n '1s/^name: //p' <<<"$config")"
[ -n "$project" ] || { echo "pg-restore: could not read the compose project; set COMPOSE_FILE and COMPOSE_PROJECT_NAME" >&2; exit 2; }
grep -qx pg-backup <<<"$services" \
  || { echo "pg-restore: the pg-backup service is not in this stack; add docker-compose.postgres-backup.yml and the postgres profile" >&2; exit 2; }

restore=(pgbackrest restore ${set_label:+"--set=$set_label"})
case "$target" in
  latest) ;;
  lsn:*) restore+=(--type=lsn "--target=${target#lsn:}" --target-action=promote) ;;
  *) restore+=(--type=time "--target=$target" --target-action=promote) ;;
esac
if [ "$in_place" = 1 ]; then
  volume="${project}_postgres_data"
  # pgBackRest also refuses while the server runs (postmaster.pid); this stops earlier and says what to do
  if [ -n "$(compose ps -q --status running postgres)" ]; then
    echo "pg-restore: the postgres service is running; stop the stack first (./install.sh --stop or scripts/pipeshub-compose.sh stop)" >&2; exit 3
  fi
  # --delta keeps the files that already match the backup; archiving stays on, so the restored server continues the backup history
  restore+=(--delta)
else
  volume="${into:-${project}_pg_restore_$(date -u +%Y%m%d%H%M%S)}"
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    echo "pg-restore: volume $volume already exists; name a new one with --into" >&2; exit 3
  fi
  docker volume create "$volume" >/dev/null
  # a copy must never archive into the repository it was restored from
  restore+=(--archive-mode=off)
fi

start=$(date +%s)
echo "pg-restore: restoring ${target} into volume ${volume}"
# A new named volume takes the owner of the image's /pgbackrest-restore (postgres); one that does not (another volume driver, or a volume
# made elsewhere) is handed to postgres here, as root, so that pgBackRest itself never runs as root. pg-backup drops every capability, and
# root without them may neither chown (CHOWN) nor chmod a directory it no longer owns (FOWNER); this step takes back only those two.
compose run --rm --no-deps -T -u root --cap-add CHOWN --cap-add FOWNER -v "$volume:/pgbackrest-restore" --entrypoint sh pg-backup \
  -c 'chown postgres:postgres /pgbackrest-restore && chmod 0700 /pgbackrest-restore'
# pg1-path through the environment, not the command line: pgBackRest copies command-line options into the restored restore_command, and
# the server that replays the WAL has its data at another path (the stack's server at /var/lib/postgresql/data)
compose run --rm --no-deps -T -v "$volume:/pgbackrest-restore" -e PGBACKREST_PG1_PATH=/pgbackrest-restore pg-backup "${restore[@]}"

if [ "$in_place" = 1 ]; then
  echo "pg-restore: restored in $(( $(date +%s) - start ))s. Start the stack (./install.sh --no-pull or scripts/pipeshub-compose.sh up -d); the server replays WAL to the target first."
  exit 0
fi

# Recovery refuses to run with max_connections and four other sizes below the original server's (https://www.postgresql.org/docs/17/
# hot-standby.html, 26.4.3), so the copy takes them from the restored control file.
# shortcut: a size raised on the original after this backup pauses recovery (below: the script stops and shows the log naming it);
# restart the copy with that setting raised.
sizes=$(compose run --rm --no-deps -T -v "$volume:/pgbackrest-restore" --entrypoint pg_controldata pg-backup /pgbackrest-restore |
  awk -F': *' '{ n = $1; sub(/ setting$/, "", n) }
    n ~ /^max_(connections|worker_processes|wal_senders)$/ { print "-c " n "=" $2 }
    n == "max_prepared_xacts" { print "-c max_prepared_transactions=" $2 }
    n == "max_locks_per_xact" { print "-c max_locks_per_transaction=" $2 }')
[ "$(echo "$sizes" | grep -c .)" = 5 ] || { echo "pg-restore: could not read the restored control file:" >&2; echo "$sizes" >&2; exit 1; }
# The preload list of postgres/pg-entrypoint.sh (pg_textsearch must be preloaded), a socket of its own, no archiving.
# shortcut: the copy runs within the pg-backup service's memory limit (PG_BACKUP_MEMORY_LIMIT, 1G); raise it before restoring a large
# database you want to query hard, or restore --in-place.
# shellcheck disable=SC2086 # $sizes is "-c name=value" words
compose run -d --no-deps --name "$volume" -v "$volume:/pgbackrest-restore" \
  -e PGDATA=/pgbackrest-restore -e PGBACKREST_PG1_PATH=/pgbackrest-restore --entrypoint docker-entrypoint.sh pg-backup \
  postgres -c shared_preload_libraries=pg_stat_statements,auto_explain,pg_textsearch -c unix_socket_directories=/tmp \
  -c archive_mode=off $sizes >/dev/null
# Recovery is over once the server accepts writes (it promotes at the target). A target past the end of the archive stops the server;
# a setting the WAL needs higher than the copy has pauses it ("If the standby replays WAL showing a higher value than its own, it logs a
# warning and pauses recovery", https://www.postgresql.org/docs/17/hot-standby.html 26.4.3); so does nothing else here, since the
# target action is promote. Either way, and past PG_RESTORE_WAIT_SECS, stop with the server's log and leave the copy to inspect.
deadline=$(( $(date +%s) + ${PG_RESTORE_WAIT_SECS:-21600} ))
while :; do
  if [ "$(docker inspect -f '{{.State.Running}}' "$volume")" != true ]; then
    echo "pg-restore: the restored server stopped; its log:" >&2; docker logs --tail 30 "$volume" >&2; exit 1
  fi
  state="$(docker exec "$volume" psql -h /tmp -U postgres -d pipeshub -tAc \
    "select case when not pg_is_in_recovery() then 'promoted' when pg_is_wal_replay_paused() then 'paused' end" 2>/dev/null)" || state=""
  [ "$state" = promoted ] && break
  if [ "$state" = paused ]; then
    echo "pg-restore: recovery paused before reaching the target; the copy's log says why:" >&2; docker logs --tail 30 "$volume" >&2; exit 1
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "pg-restore: recovery not finished after PG_RESTORE_WAIT_SECS=${PG_RESTORE_WAIT_SECS:-21600}s; the copy's log:" >&2
    docker logs --tail 30 "$volume" >&2; exit 1
  fi
  sleep 2
done
echo "pg-restore: restored and running in $(( $(date +%s) - start ))s: container ${volume}, volume ${volume}"
echo "pg-restore: docker exec -it ${volume} psql -h /tmp -U postgres -d pipeshub"
