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
# A drop is logged by pgBackRest as the warning "dropped WAL file '<name>' because archive queue exceeded <size>"
# (src/command/archive/push/push.c). log-level-stderr "specifies which log levels will output to stderr rather than stdout (specified by
# log-level-console)", default off (https://pgbackrest.org/configuration.html#section-log): this run sends warnings to stderr unless a
# more verbose stderr level is set, so the warning is seen even with the console level off.
#
# Each drop adds its own file to the gap directory (named by its UTC time, so the first sorts first), which the pg-backup service shares:
# restores have a gap until its next full backup, which removes only the files it found before it started, so a drop during that backup
# is never lost.
if [ -z "${PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX+set}" ]; then
  export PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX=4GiB
elif [ -z "$PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX" ]; then
  unset PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX
fi
case "${PGBACKREST_LOG_LEVEL_STDERR:-off}" in off|error) stderr=--log-level-stderr=warn ;; *) stderr= ;; esac
out="$(pgbackrest ${stderr:+"$stderr"} archive-push "$1" 2>&1)"; rc=$?
[ -n "$out" ] && printf '%s\n' "$out" >&2
case "$out" in
  *"dropped WAL file"*)
    gaps="${PG_WAL_GAP_DIR:-/var/run/postgresql/pgbackrest-wal-gap.d}"
    { mkdir -p "$gaps" && : > "$gaps/$(date -u '+%Y-%m-%dT%H:%M:%S.%NZ')-$$"; } ||
      echo "pgbackrest-archive-push: WAL was dropped and $gaps could not be written: point-in-time restore has a gap" >&2 ;;
esac
exit $rc
