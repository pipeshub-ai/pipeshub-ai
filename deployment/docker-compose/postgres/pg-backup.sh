#!/bin/bash
# The `pg-backup` service (docker-compose.postgres-backup.yml): creates the pgBackRest stanza, then takes a full backup when the newest
# one is older than PG_BACKUP_FULL_DAYS (or there is none) and an incremental one when the newest backup of any type is older than
# PG_BACKUP_INCR_HOURS. The schedule is read back from the repository (`pgbackrest info`), so a restart neither skips nor repeats a backup.
# Every pgBackRest setting comes from PGBACKREST_* variables ("Any option may be set in an environment variable using the PGBACKREST_
# prefix", https://pgbackrest.org/command.html). WAL is archived by the server itself (archive_command, set by pg-entrypoint.sh).
set -uo pipefail
full_s=$(( ${PG_BACKUP_FULL_DAYS:-7} * 86400 ))
incr_s=$(( ${PG_BACKUP_INCR_HOURS:-6} * 3600 ))
retry_s="${PG_BACKUP_RETRY_SECS:-300}"
ok="${PG_BACKUP_OK_FILE:-/tmp/pg-backup.ok}"   # the healthcheck: present only while the last attempt succeeded

# "<seconds since newest full> <seconds since newest backup>" from the repository; a missing backup counts as infinitely old.
ages() {
  pgbackrest --output=json info | perl -MJSON::PP -e '
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
  if ! read -r since_full since_any < <(ages); then
    rm -f "$ok"; echo "pg-backup: could not read the repository; retrying in ${retry_s}s" >&2; sleep "$retry_s"; continue
  fi
  if [ "$since_full" -ge "$full_s" ]; then type=full
  elif [ "$since_any" -ge "$incr_s" ]; then type=incr
  else sleep $(( incr_s - since_any < 60 ? 60 : incr_s - since_any )); continue
  fi
  echo "pg-backup: starting a $type backup"
  if pgbackrest backup --type="$type"; then
    touch "$ok"; echo "pg-backup: $type backup done"
  else
    rm -f "$ok"; echo "pg-backup: the $type backup failed; retrying in ${retry_s}s" >&2; sleep "$retry_s"
  fi
done
