#!/bin/bash
# The `pg-backup` service (docker-compose.postgres-backup.yml): creates the pgBackRest stanza, then takes a full backup when the newest
# one is older than PG_BACKUP_FULL_DAYS (or there is none) and an incremental one when the newest backup of any type is older than
# PG_BACKUP_INCR_HOURS. The schedule is read back from the repository (`pgbackrest info`), so a restart neither skips nor repeats a backup.
# Every pgBackRest setting comes from PGBACKREST_* variables ("Any option may be set in an environment variable using the PGBACKREST_
# prefix", https://pgbackrest.org/command.html). WAL is archived by the server itself (pgbackrest-archive-push.sh, set by pg-entrypoint.sh).
#
# When the archive queue cap made the server drop WAL (pgbackrest-archive-push.sh leaves the gap file in the shared socket directory),
# restores cannot cross that point until a newer full backup exists: the service turns unhealthy, says so, and takes a full backup at once.
set -uo pipefail
full_s=$(( ${PG_BACKUP_FULL_DAYS:-7} * 86400 ))
incr_s=$(( ${PG_BACKUP_INCR_HOURS:-6} * 3600 ))
retry_s="${PG_BACKUP_RETRY_SECS:-300}"
ok="${PG_BACKUP_OK_FILE:-/tmp/pg-backup.ok}"   # the healthcheck: present only while the last attempt succeeded and WAL has no gap
gap="${PG_WAL_GAP_FILE:-/var/run/postgresql/pgbackrest-wal-gap}"
started="${ok}.started"

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
  if [ -e "$gap" ]; then
    rm -f "$ok"
    echo "pg-backup: the server dropped WAL from $(cat "$gap" 2>/dev/null) to $(date -u -r "$gap" '+%Y-%m-%d %H:%M:%S UTC') because the" \
         "archive queue exceeded PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX (the repository was unreachable or too slow): point-in-time restore" \
         "has a gap from then until the next full backup. Taking a full backup now." >&2
    type=full
  elif ! read -r since_full since_any < <(ages); then
    rm -f "$ok"; echo "pg-backup: could not read the repository; retrying in ${retry_s}s" >&2; sleep "$retry_s"; continue
  elif [ "$since_full" -ge "$full_s" ]; then type=full
  elif [ "$since_any" -ge "$incr_s" ]; then type=incr
  else
    # at most a minute, so a gap is noticed within one
    left=$(( incr_s - since_any )); sleep $(( left > 60 ? 60 : left )); continue
  fi
  echo "pg-backup: starting a $type backup"
  touch "$started"
  if pgbackrest backup --type="$type"; then
    # a gap that opened while this backup ran is not closed by it
    [ "$type" = full ] && [ -e "$gap" ] && ! [ "$gap" -nt "$started" ] && rm -f "$gap" && echo "pg-backup: the gap in the WAL archive is closed by this full backup"
    if [ -e "$gap" ]; then rm -f "$ok"; else touch "$ok"; fi
    echo "pg-backup: $type backup done"
  else
    rm -f "$ok"; echo "pg-backup: the $type backup failed; retrying in ${retry_s}s" >&2; sleep "$retry_s"
  fi
done
