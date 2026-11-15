#!/bin/bash
# The `pg-backup` service (docker-compose.postgres-backup.yml): creates the pgBackRest stanza, then takes a full backup when the newest
# one is older than PG_BACKUP_FULL_DAYS (or there is none) and an incremental one when the newest backup of any type is older than
# PG_BACKUP_INCR_HOURS. The schedule is read back from the repository (`pgbackrest info`), so a restart neither skips nor repeats a backup.
# Every pgBackRest setting comes from PGBACKREST_* variables ("Any option may be set in an environment variable using the PGBACKREST_
# prefix", https://pgbackrest.org/command.html). WAL is archived by the server itself (pgbackrest-archive-push.sh, set by pg-entrypoint.sh).
#
# When the archive queue cap made the server drop WAL (pgbackrest-archive-push.sh adds a file per drop to the gap directory in the shared
# socket directory), restores cannot cross that point until a newer full backup exists: the service turns unhealthy, says so, and takes a
# full backup at once. That backup removes only the files it listed before it started, so a drop during the backup keeps the gap open.
set -uo pipefail
full_s=$(( ${PG_BACKUP_FULL_DAYS:-7} * 86400 ))
incr_s=$(( ${PG_BACKUP_INCR_HOURS:-6} * 3600 ))
retry_s="${PG_BACKUP_RETRY_SECS:-300}"
ok="${PG_BACKUP_OK_FILE:-/tmp/pg-backup.ok}"   # the healthcheck: present only while the last attempt succeeded and WAL has no gap
gaps="${PG_WAL_GAP_DIR:-/var/run/postgresql/pgbackrest-wal-gap.d}"
drops() { ls -A "$gaps" 2>/dev/null; }   # one name per dropped segment, in WAL order

gap_report() {
  local d f lost=""
  d="$(drops)"
  for f in $d; do [ -z "$lost" ] && lost="$(head -1 "$gaps/$f" 2>/dev/null)"; done
  echo "pg-backup: the server dropped WAL segments $(head -1 <<<"$d") to $(tail -1 <<<"$d") (noticed" \
       "$(date -u -r "$gaps/$(head -1 <<<"$d")" '+%Y-%m-%d %H:%M:%S UTC') to $(date -u -r "$gaps/$(tail -1 <<<"$d")" '+%Y-%m-%d %H:%M:%S UTC'))" \
       "because the archive queue exceeded PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX (the repository was unreachable or too slow)."
  if [ -n "$lost" ]; then
    echo "pg-backup: point-in-time restore cannot reach the transaction committed at $lost or anything after it until the next full" \
         "backup; restore to an earlier time (a time just before it can still fail when no later transaction remains to stop at)."
  else
    echo "pg-backup: the dropped segments hold no commit this service could read: point-in-time restore cannot reach past segment" \
         "$(head -1 <<<"$d"), which may hold transactions from before the time it was noticed, until the next full backup."
  fi
  echo "pg-backup: taking a full backup now."
}

# "<seconds since newest full> <seconds since newest backup>" from the repository; a missing backup counts as infinitely old.
ages() {
  pgbackrest --log-level-console=warn --output=json info | perl -MJSON::PP -e '
    my $now = time; my ($full, $any) = (0, 0);
    for my $s (@{ decode_json(join "", <STDIN>) }) {
      for my $b (@{ $s->{backup} || [] }) {
        my $t = $b->{timestamp}{stop};
        $any = $t if $t > $any;
        $full = $t if $b->{type} eq "full" && $t > $full;
      }
    }
    printf "%d %d\n", $full ? $now - $full : 1e15, $any ? $now - $any : 1e15;'
}

until pgbackrest stanza-create && pgbackrest check; do
  rm -f "$ok"; echo "pg-backup: the stanza could not be created or checked; retrying in ${retry_s}s" >&2; sleep "$retry_s"
done
while :; do
  if [ -n "$(drops)" ]; then
    rm -f "$ok"
    gap_report >&2
    type=full
  elif ! read -r since_full since_any < <(ages); then
    rm -f "$ok"; echo "pg-backup: could not read the repository; retrying in ${retry_s}s" >&2; sleep "$retry_s"; continue
  elif [ "$since_full" -ge "$full_s" ]; then type=full
  elif [ "$since_any" -ge "$incr_s" ]; then type=incr
  else
    # nothing due: the repository holds backups within both intervals, which is healthy (also right after a restart)
    touch "$ok"
    # at most a minute, so a gap is noticed within one
    left=$(( incr_s - since_any )); sleep $(( left > 60 ? 60 : left )); continue
  fi
  echo "pg-backup: starting a $type backup"
  # the first backup of a new repository can take hours: healthy while it runs, unless WAL was dropped
  if [ "${since_any:-0}" -ge "$full_s" ] && [ "${since_full:-0}" -ge 1000000000000000 ] && [ -z "$(drops)" ]; then touch "$ok"; fi
  covered="$(drops)"
  if pgbackrest backup --type="$type"; then
    if [ "$type" = full ] && [ -n "$covered" ]; then
      (cd "$gaps" && echo "$covered" | xargs rm -f --)
      echo "pg-backup: the drops before this full backup are covered by it"
    fi
    if [ -n "$(drops)" ]; then rm -f "$ok"; else touch "$ok"; fi
    echo "pg-backup: $type backup done"
  else
    rm -f "$ok"; echo "pg-backup: the $type backup failed; retrying in ${retry_s}s" >&2; sleep "$retry_s"
  fi
done
