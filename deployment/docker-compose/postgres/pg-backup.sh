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
#
# Restoring up to a gap needs the newest backup taken before it (`pg-restore.sh latest --set <label>`), which the next full backups would
# expire by retention. pgBackRest has no per-backup pin, so while drops are marked or a gap hold exists, backups run with --no-expire-auto
# (expire-auto: "Automatically run the expire command after a successful backup", default y; "The no- prefix may be used to set a boolean
# option to false on the command-line", https://pgbackrest.org/command.html): nothing expires until an operator runs
#   pg-backup.sh clear-gap-hold
# which removes the hold and expires by retention at once (the expire command "can also be run by the user"). A hold nobody clears is
# released after PG_BACKUP_GAP_HOLD_DAYS (default 14), so the repository cannot grow until backups fail; the held backup then falls
# under normal retention.
set -uo pipefail
full_s=$(( ${PG_BACKUP_FULL_DAYS:-7} * 86400 ))
incr_s=$(( ${PG_BACKUP_INCR_HOURS:-6} * 3600 ))
retry_s="${PG_BACKUP_RETRY_SECS:-300}"
ok="${PG_BACKUP_OK_FILE:-/tmp/pg-backup.ok}"   # the healthcheck: present only while the last attempt succeeded and WAL has no gap
gaps="${PG_WAL_GAP_DIR:-/var/run/postgresql/pgbackrest-wal-gap.d}"
drops() { ls -A "$gaps" 2>/dev/null; }   # one name per dropped segment, in WAL order
hold="${PG_WAL_GAP_HOLD:-/var/run/postgresql/pgbackrest-gap-hold}"   # "<label of the newest backup before the gap> <first dropped segment>"
hold_s=$(( ${PG_BACKUP_GAP_HOLD_DAYS:-14} * 86400 ))   # the hold's age is its file's modification time

if [ "${1:-}" = clear-gap-hold ]; then
  if [ -n "$(drops)" ]; then echo "pg-backup: WAL drops are still uncovered by a full backup; the hold stays" >&2; exit 1; fi
  if [ -e "$hold" ]; then echo "pg-backup: clearing the gap hold ($(cat "$hold")); expiring by retention now"; rm -f "$hold"; fi
  exec pgbackrest expire
fi

# The newest backup that stopped before the first drop was noticed: one that stopped later needed WAL past the gap (archive-check).
pre_gap_label() {
  local t; t="$(stat -c %Y "$gaps/$(drops | head -1)")" || return 1
  pgbackrest --log-level-console=warn --output=json info | perl -MJSON::PP -e '
    my ($t) = @ARGV; my $l = "";
    for my $s (@{ decode_json(join "", <STDIN>) }) { for my $b (@{ $s->{backup} || [] }) { $l = $b->{label} if $b->{timestamp}{stop} < $t } }
    print $l;' "$t"
}

# Recovery replays segments in order and cannot cross a missing one, so the gap starts at the earliest dropped segment, whatever later ones
# hold. Its first commit, if known, is the first transaction lost; the last reachable point is the last commit before that segment.
gap_report() {
  local d first lost
  d="$(drops)"; first="$(head -1 <<<"$d")"
  lost="$(head -1 "$gaps/$first" 2>/dev/null)"
  echo "pg-backup: the server dropped WAL segments $(head -1 <<<"$d") to $(tail -1 <<<"$d") (noticed" \
       "$(date -u -r "$gaps/$(head -1 <<<"$d")" '+%Y-%m-%d %H:%M:%S UTC') to $(date -u -r "$gaps/$(tail -1 <<<"$d")" '+%Y-%m-%d %H:%M:%S UTC'))" \
       "because the archive queue exceeded PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX (the repository was unreachable or too slow)."
  if [ -n "$lost" ]; then
    echo "pg-backup: point-in-time restore cannot reach past the start of segment $first until the next full backup: its first" \
         "transaction, committed at $lost, is the first one lost, so the last reachable point is the last commit before it."
  else
    echo "pg-backup: point-in-time restore cannot reach past segment $first until the next full backup: from segment $first" \
         "(no commit time known), so the last reachable point is before $first, possibly before the time it was noticed."
  fi
  local label; label="$(cut -d' ' -f1 "$hold" 2>/dev/null)"
  echo "pg-backup: to restore up to the gap, restore the end of the archive from the newest backup taken before it:" \
       "scripts/pg-restore.sh latest --set ${label:-<its label from pgbackrest info>}; a time target needs a later commit before the gap to stop at."
  echo "pg-backup: nothing is expired while this gap is held, so that backup stays; when you no longer need it:" \
       "docker compose exec pg-backup pg-backup.sh clear-gap-hold (released by itself after PG_BACKUP_GAP_HOLD_DAYS=$(( hold_s / 86400 )) days)"
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
  if [ -e "$hold" ] && [ $(( $(date +%s) - $(stat -c %Y "$hold") )) -ge "$hold_s" ]; then
    echo "pg-backup: RELEASING THE GAP HOLD ($(cat "$hold")) after PG_BACKUP_GAP_HOLD_DAYS=$(( hold_s / 86400 )) days: expiry resumes," \
         "so the backup before the gap now falls under normal retention and restoring up to the gap may soon be impossible" >&2
    rm -f "$hold"
  fi
  if [ -n "$(drops)" ]; then
    rm -f "$ok"
    if [ ! -s "$hold" ]; then
      label="$(pre_gap_label 2>/dev/null)" || label=""
      if [ -n "$label" ]; then echo "$label $(drops | head -1)" > "$hold"; fi
    fi
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
  expire=; if [ -n "$(drops)" ] || [ -e "$hold" ]; then
    expire=--no-expire-auto; echo "pg-backup: keeping every backup (no expire) while a WAL gap is held: $(cat "$hold" 2>/dev/null)"
  fi
  if pgbackrest backup --type="$type" ${expire:+"$expire"}; then
    if [ "$type" = full ] && [ -n "$covered" ]; then
      # the hold, if the repository could not be read while the drops were noticed (the drop files date the gap)
      if [ ! -s "$hold" ]; then
        label="$(pre_gap_label 2>/dev/null)" || label=""
        if [ -n "$label" ]; then echo "$label $(drops | head -1)" > "$hold"; fi
      fi
      (cd "$gaps" && echo "$covered" | xargs rm -f --)
      echo "pg-backup: the drops before this full backup are covered by it"
    fi
    if [ -n "$(drops)" ]; then rm -f "$ok"; else touch "$ok"; fi
    echo "pg-backup: $type backup done"
  else
    rm -f "$ok"; echo "pg-backup: the $type backup failed; retrying in ${retry_s}s" >&2; sleep "$retry_s"
  fi
done
