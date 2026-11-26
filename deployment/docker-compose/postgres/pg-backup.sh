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
# expire by retention. pgBackRest has no per-backup pin, so backups always run with --no-expire-auto (expire-auto: "Automatically run the
# expire command after a successful backup", default y; "The no- prefix may be used to set a boolean option to false on the command-line",
# https://pgbackrest.org/command.html), and `pgbackrest expire` runs on its own after a backup only when, checked again then, no drop is
# uncovered and no gap is held (the expire command "can also be run by the user"). Each gap gets its own hold, named by its first dropped
# segment and holding its own pre-gap label; the drops of a gap are cleared only once its hold is recorded. An operator releases holds with
#   pg-backup.sh clear-gap-hold [<first dropped segment>]
# which, under the lock the scheduler takes, expires by retention first (when no other hold remains) and removes the holds only once that
# succeeded. A hold nobody clears is released after PG_BACKUP_GAP_HOLD_DAYS (default 14), so the repository cannot grow until backups
# fail; its backup then falls under normal retention.
set -uo pipefail
full_s=$(( ${PG_BACKUP_FULL_DAYS:-7} * 86400 ))
incr_s=$(( ${PG_BACKUP_INCR_HOURS:-6} * 3600 ))
retry_s="${PG_BACKUP_RETRY_SECS:-300}"
ok="${PG_BACKUP_OK_FILE:-/tmp/pg-backup.ok}"   # the healthcheck: present only while the last attempt succeeded and WAL has no gap
gaps="${PG_WAL_GAP_DIR:-/var/run/postgresql/pgbackrest-wal-gap.d}"
drops() { ls -A "$gaps" 2>/dev/null; }   # one name per dropped segment, in WAL order
holds="${PG_WAL_GAP_HOLDS:-/var/run/postgresql/pgbackrest-gap-hold.d}"   # one file per gap: <first dropped segment>, holding the label
held() { ls -A "$holds" 2>/dev/null; }
hold_s=$(( ${PG_BACKUP_GAP_HOLD_DAYS:-14} * 86400 ))   # a hold's age is its file's modification time
lock="${PG_BACKUP_LOCK:-/tmp/pg-backup.lock}"   # taken around each backup and its expire, and by clear-gap-hold
# Prometheus metrics (pg-backup-metrics.pl): written after every repository read and backup attempt, served on PG_BACKUP_METRICS_PORT
# when it is set. The backup times are exported as timestamps, so their age keeps growing in Prometheus even if this loop stops.
metrics="${PG_BACKUP_METRICS_FILE:-/tmp/pg-backup.prom}"
state="${PG_BACKUP_STATE_DIR:-/tmp/pg-backup.d}"   # the last readable `pgbackrest info` and the last attempt
metrics_pl="$(dirname "${BASH_SOURCE[0]}")/pg-backup-metrics.pl"

if [ "${1:-}" = clear-gap-hold ]; then
  # the lock first: drops and holds are read, and expire runs, all under it
  exec 9>"$lock"
  flock -w "${PG_BACKUP_LOCK_WAIT_SECS:-60}" 9 || { echo "pg-backup: a backup is running; try again when it ends" >&2; exit 1; }
  if [ -n "$(drops)" ]; then echo "pg-backup: WAL drops are still uncovered by a full backup; the holds stay" >&2; exit 1; fi
  targets="${2:-$(held)}"
  [ -n "$targets" ] || { echo "pg-backup: no gap is held"; exit 0; }
  for t in $targets; do [ -e "$holds/$t" ] || { echo "pg-backup: no hold for gap $t" >&2; exit 1; }; done
  remaining="$(held | grep -vxF -f <(tr ' ' '\n' <<<"$targets"))"
  if [ -z "$remaining" ]; then
    echo "pg-backup: expiring by retention before releasing the hold(s)"
    pgbackrest expire || { echo "pg-backup: expire failed; the holds stay" >&2; exit 1; }
  fi
  for t in $targets; do echo "pg-backup: released the hold on gap $t (backup $(cat "$holds/$t"))"; rm -f "$holds/$t"; done
  exit 0
fi

# Records the hold of the current gap (named by its first dropped segment) unless it exists: the newest backup that stopped before the
# first drop was noticed (one that stopped later needed WAL past the gap, archive-check), or "none". Fails when the repository cannot be
# read, so the hold stays pending and the drops stay.
record_hold() {
  local first t label
  first="$(drops | head -1)"; [ -n "$first" ] || return 0
  [ -e "$holds/$first" ] && return 0
  t="$(stat -c %Y "$gaps/$first")" || return 1
  # a stanza whose status is not ok (0) or "no valid backups" (2) is not read: its backup list can be empty while backups exist (as
  # with the archive unreachable), which would record "none" for good
  label="$(pgbackrest --log-level-console=warn --output=json info | perl -MJSON::PP -e '
    my ($t) = @ARGV; my $l = "none";
    for my $s (@{ decode_json(join "", <STDIN>) }) {
      my $c = $s->{status}{code} // 99; exit 1 unless $c == 0 || $c == 2;
      for my $b (@{ $s->{backup} || [] }) { $l = $b->{label} if $b->{timestamp}{stop} < $t }
    }
    print $l;' "$t")" || return 1
  mkdir -p "$holds" && echo "$label" > "$holds/$first"
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
  local label; label="$(cat "$holds/$first" 2>/dev/null)"
  case "$label" in
    "") label="<its label from pgbackrest info: the repository cannot be read yet>" ;;
    none) label="<none: no backup finished before this gap>" ;;
  esac
  echo "pg-backup: to restore up to the gap, restore the end of the archive from the newest backup taken before it:" \
       "scripts/pg-restore.sh latest --set $label; a time target needs a later commit before the gap to stop at."
  echo "pg-backup: nothing is expired while this gap is held, so that backup stays; when you no longer need it:" \
       "scripts/pipeshub-compose.sh exec pg-backup pg-backup.sh clear-gap-hold $first (released by itself after PG_BACKUP_GAP_HOLD_DAYS=$(( hold_s / 86400 )) days)"
  echo "pg-backup: taking a full backup now."
}

refresh_metrics() {
  "$metrics_pl" write "$state/info.new" "$state" "$gaps" "$full_s" "$incr_s" "$metrics" || echo "pg-backup: could not write the metrics" >&2
}
attempted() { echo "$(date +%s) $1" > "$state/attempt"; refresh_metrics; }   # 1: the backup succeeded, 0: it failed
srv=""
serve_metrics() {   # (re)started when it is not running, so a crashed server comes back within a minute
  [ -n "${PG_BACKUP_METRICS_PORT:-}" ] || return 0
  if [ -z "$srv" ] || ! kill -0 "$srv" 2>/dev/null; then "$metrics_pl" serve "$PG_BACKUP_METRICS_PORT" "$metrics" 9>&- & srv=$!; fi
}

# "<seconds since newest full> <seconds since newest backup>" from the repository; a missing backup counts as infinitely old.
ages() {
  pgbackrest --log-level-console=warn --output=json info > "$state/info.new" || : > "$state/info.new"
  refresh_metrics
  perl -MJSON::PP -e '
    my $now = time; my ($full, $any) = (0, 0);
    for my $s (@{ decode_json(join "", <STDIN>) }) {
      for my $b (@{ $s->{backup} || [] }) {
        my $t = $b->{timestamp}{stop};
        $any = $t if $t > $any;
        $full = $t if $b->{type} eq "full" && $t > $full;
      }
    }
    printf "%d %d\n", $full ? $now - $full : 1e15, $any ? $now - $any : 1e15;' < "$state/info.new"
}

mkdir -p "$state"; : > "$state/info.new"; refresh_metrics; serve_metrics
until pgbackrest stanza-create && pgbackrest check; do
  : > "$state/info.new"; refresh_metrics
  rm -f "$ok"; echo "pg-backup: the stanza could not be created or checked; retrying in ${retry_s}s" >&2; sleep "$retry_s"
done
while :; do
  serve_metrics
  for g in $(held); do
    if [ $(( $(date +%s) - $(stat -c %Y "$holds/$g") )) -ge "$hold_s" ]; then
      echo "pg-backup: RELEASING THE GAP HOLD on gap $g (backup $(cat "$holds/$g")) after PG_BACKUP_GAP_HOLD_DAYS=$(( hold_s / 86400 ))" \
           "days: that backup now falls under normal retention, and restoring up to that gap may soon be impossible" >&2
      rm -f "$holds/$g"
    fi
  done
  if [ -n "$(drops)" ]; then
    rm -f "$ok"
    record_hold 2>/dev/null || true
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
  exec 9>"$lock"; flock 9
  if pgbackrest backup --type="$type" --no-expire-auto; then
    if [ "$type" = full ] && [ -n "$covered" ]; then
      # the gap's hold first, if the repository could not be read while the drops were noticed; without it the drops stay
      if record_hold 2>/dev/null && [ -e "$holds/$(head -1 <<<"$covered")" ]; then
        (cd "$gaps" && echo "$covered" | xargs rm -f --)
        echo "pg-backup: the drops before this full backup are covered by it"
      else
        echo "pg-backup: the drops stay until the backup before the gap is recorded (the repository could not be read)" >&2
      fi
    fi
    if [ -n "$(drops)" ]; then rm -f "$ok"; else touch "$ok"; fi
    attempted 1
    echo "pg-backup: $type backup done"
    # checked again now: a drop during the backup, or a held gap, keeps every backup
    if [ -z "$(drops)" ] && [ -z "$(held)" ]; then
      pgbackrest expire || echo "pg-backup: expire failed; it runs again after the next backup" >&2
    else
      echo "pg-backup: keeping every backup (no expire) while a WAL gap is held: $(held | tr '\n' ' ')"
    fi
  else
    failed=1; rm -f "$ok"; attempted 0; echo "pg-backup: the $type backup failed; retrying in ${retry_s}s" >&2
  fi
  flock -u 9
  if [ "${failed:-0}" = 1 ]; then failed=0; sleep "$retry_s"; fi
done
