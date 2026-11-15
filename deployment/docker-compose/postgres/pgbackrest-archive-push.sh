#!/bin/sh
# archive_command of the server when backups are on (pg-entrypoint.sh): `pgbackrest archive-push %p` with the archive queue capped.
# The name contains "pgbackrest": pgBackRest's check command refuses an archive_command without it ("archive_command '...' must
# contain pgbackrest", src/command/check/common.c).
#
# The cap: with the repository unreachable, PostgreSQL keeps every unarchived WAL segment and the data volume fills until the server
# stops. Beyond archive-push-queue-max "pgBackRest will notify PostgreSQL that the WAL was successfully archived, then DROP IT ... the
# archive log stream will be interrupted and PITR will not be possible past that point. A new backup will be required to regain full
# restore capability" (https://pgbackrest.org/configuration.html#section-archive/option-archive-push-queue-max). Unset, the cap is 4GiB;
# set it in pgbackrest.env to change it, or set it empty for no cap (pgBackRest's own default).
#
# A drop is logged by pgBackRest as "dropped WAL file '<name>' because archive queue exceeded <size>" (src/command/archive/push/push.c);
# the gap file tells the pg-backup service, which shares this directory, that restores now have a gap until its next full backup.
if [ -z "${PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX+set}" ]; then
  export PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX=4GiB
elif [ -z "$PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX" ]; then
  unset PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX
fi
out="$(pgbackrest archive-push "$1" 2>&1)"; rc=$?
[ -n "$out" ] && printf '%s\n' "$out" >&2
case "$out" in
  *"dropped WAL file"*)
    gap="${PG_WAL_GAP_FILE:-/var/run/postgresql/pgbackrest-wal-gap}"
    # the file holds the time of the first drop, its modification time is the last one
    { if [ -e "$gap" ]; then touch "$gap"; else date -u '+%Y-%m-%d %H:%M:%S UTC' > "$gap"; fi; } ||
      echo "pgbackrest-archive-push: WAL was dropped and $gap could not be written: point-in-time restore has a gap" >&2 ;;
esac
exit $rc
