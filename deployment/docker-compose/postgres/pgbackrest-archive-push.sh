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
# Each drop adds its own file to the gap directory, which the pg-backup service shares: named by the dropped segment (so WAL order is name
# order), holding the commit time of the segment's first transaction, if any (pg_waldump reads it while the segment is still in pg_wal;
# endseg = startseg, "stop after reading the specified WAL segment file", https://www.postgresql.org/docs/17/pgwaldump.html, and a
# timeout keep archive_command short),
# with the time the drop was noticed as its modification time. The earliest such commit is the first transaction a restore can no longer
# reach. Restores have a gap until the next full backup, which removes only the files it found before it started, so a drop during that
# backup is never lost.
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
    seg="$(printf '%s\n' "$out" | sed -n "s/.*dropped WAL file '\([0-9A-F]\{24\}\)'.*/\1/p" | head -1)"
    first="$(timeout 10 pg_waldump --rmgr=Transaction "$1" "$1" 2>/dev/null | sed -n 's/.*desc: COMMIT \([0-9-]* [0-9:.]* [A-Za-z0-9+-]*\).*/\1/p' | head -1)"
    # as a time pg-restore.sh takes: pgBackRest cannot select a backup by "... UTC" (error 029), it can by "...+00"
    [ -n "$first" ] && first="$(date -u -d "$first" '+%Y-%m-%d %H:%M:%S.%6N+00' 2>/dev/null || echo "$first")"
    { mkdir -p "$gaps" && printf '%s\n' "$first" > "$gaps/${seg:-unknown-$(date -u +%s%N)-$$}"; } ||
      echo "pgbackrest-archive-push: WAL was dropped and $gaps could not be written: point-in-time restore has a gap" >&2 ;;
esac
exit $rc
