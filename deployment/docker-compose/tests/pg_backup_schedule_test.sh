#!/usr/bin/env bash
# No Docker, with pgbackrest and sleep stubbed:
#   - postgres/pg-backup.sh picks the backup type from the ages of the backups in the repository (pgbackrest info), and turns to a full
#     backup and unhealthy when the archive queue cap dropped WAL; the first decision of the loop is recorded, then a stub ends the script;
#   - postgres/pgbackrest-archive-push.sh caps the archive queue by default and marks a drop for the pg-backup service.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }

cat > "$work/pgbackrest" <<'EOF'
#!/bin/sh
case "$*" in
  *info*) # STUB_INFO_FAIL_UNTIL_BACKUP: the repository cannot be read until a backup has run; STUB_INFO_FAIL: never
    if [ -n "${STUB_INFO_FAIL:-}" ]; then exit 1; fi
    if [ -n "${STUB_INFO_FAIL_UNTIL_BACKUP:-}" ] && ! grep -q backup "$STUB_LOG"; then exit 1; fi
    cat "$STUB_INFO" ;;
  *backup*) # the first STUB_BACKUPS backups (default none) return; the next one ends the script
    echo "$*" >> "$STUB_LOG"; n=$(grep -c backup "$STUB_LOG")
    if [ -e "$PG_BACKUP_OK_FILE" ]; then echo "healthy during backup" >> "$STUB_LOG.health"; else echo "unhealthy during backup" >> "$STUB_LOG.health"; fi
    [ -z "${STUB_DROP_DURING_BACKUP:-}" ] || : > "$STUB_DROP_DURING_BACKUP"
    # what Prometheus would read while this backup runs: the metrics file, and (STUB_SCRAPE_PORT) the served endpoint
    cp "$PG_BACKUP_METRICS_FILE" "$STUB_LOG.metrics-during" 2>/dev/null || :
    if [ -n "${STUB_SCRAPE_PORT:-}" ]; then
      perl -MIO::Socket::INET -e 'for (1..50) { $c = IO::Socket::INET->new("127.0.0.1:$ARGV[0]") and last; select undef, undef, undef, 0.1 }
        print $c "GET /metrics HTTP/1.1\r\n\r\n"; print <$c>' "$STUB_SCRAPE_PORT" > "$STUB_LOG.scrape-during.$n"
    fi
    [ -z "${STUB_BACKUP_RC:-}" ] || exit "$STUB_BACKUP_RC"   # fails and lets the script go on (to its retry sleep)
    if [ "$n" -gt "${STUB_BACKUPS:-0}" ]; then kill -TERM "$PPID"; exit 1; fi ;;
  *expire*) echo "$*" >> "$STUB_LOG"; exit "${STUB_EXPIRE_RC:-0}" ;;
  *archive-push*)
    echo "queue max: ${PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX-unset}"
    # a warning goes to stderr when the stderr level (option over environment, default off) takes it, else to stdout when the console
    # level (default warn) does, else nowhere (https://pgbackrest.org/configuration.html#section-log)
    lvl="${PGBACKREST_LOG_LEVEL_STDERR:-off}"; case "$*" in *--log-level-stderr=*) lvl="${*##*--log-level-stderr=}"; lvl="${lvl%% *}" ;; esac
    if [ -n "${STUB_PUSH_OUT:-}" ]; then
      case "$lvl" in warn|info|detail|debug|trace) printf '%s' "$STUB_PUSH_OUT" >&2 ;;
        *) case "${PGBACKREST_LOG_LEVEL_CONSOLE:-warn}" in warn|info|detail|debug|trace) printf '%s' "$STUB_PUSH_OUT" ;; esac ;; esac
    fi
    exit "${STUB_PUSH_RC:-0}" ;;
esac
EOF
# the first STUB_SLEEPS sleeps return (each dropping STUB_DROP_DURING_SLEEP, as a drop noticed while the loop waits); the next ends the script
cat > "$work/sleep" <<'EOF'
#!/bin/sh
echo "sleep $1" >> "$STUB_LOG"
if [ "$(grep -c '^sleep' "$STUB_LOG")" -le "${STUB_SLEEPS:-0}" ]; then [ -z "${STUB_DROP_DURING_SLEEP:-}" ] || : > "$STUB_DROP_DURING_SLEEP"; exit 0; fi
kill -TERM "$PPID"
EOF
chmod +x "$work/pgbackrest" "$work/sleep"

decide() { # info JSON -> what the loop did first
  printf '%s' "$1" > "$work/info.json"; : > "$work/log"
  PATH="$work:$PATH" PG_BACKUP_OK_FILE="$work/ok" PG_WAL_GAP_DIR="$work/gaps" PG_WAL_GAP_HOLDS="$work/holds" PG_BACKUP_LOCK="$work/lock" STUB_INFO="$work/info.json" STUB_LOG="$work/log" \
    PG_BACKUP_METRICS_FILE="$work/metrics.prom" PG_BACKUP_STATE_DIR="$work/state" PG_BACKUP_FULL_DAYS="${FULL_DAYS:-7}" PG_BACKUP_INCR_HOURS=6 timeout 10 bash "$here/postgres/pg-backup.sh" >"$work/out" 2>&1 || true
  head -1 "$work/log"
}
now=$(date +%s)
backup() { printf '{"label":"%s-%s","type":"%s","timestamp":{"start":%d,"stop":%d}}' "$1" "$2" "$1" $((now - $2)) $((now - $2)); }
recent="[{\"status\":{\"code\":0},\"backup\":[$(backup full 86400),$(backup incr 3600)]}]"

check "no backup yet: a full one" "$(decide '[{"status":{"code":2},"backup":[]}]')" "backup --type=full --no-expire-auto"
check "no backup yet: healthy while the first full backup runs" "$(tail -1 "$work/log.health")" "healthy during backup"
check "no backup yet, full backups every 10000 days: still a full one first" "$(FULL_DAYS=10000 decide '[{"status":{"code":2},"backup":[]}]')" "backup --type=full --no-expire-auto"
rm -f "$work/ok"
check "newest full 8 days old: a full one" "$(decide "[{\"status\":{\"code\":0},\"backup\":[$(backup full 691200),$(backup incr 3600)]}]")" "backup --type=full --no-expire-auto"
check "a later full backup does not mark itself healthy before it succeeds" "$(tail -1 "$work/log.health")" "unhealthy during backup"
check "full 1 day, newest backup 7 hours old: an incremental one" \
  "$(decide "[{\"status\":{\"code\":0},\"backup\":[$(backup full 86400),$(backup incr 25200)]}]")" "backup --type=incr --no-expire-auto"
check "full 1 day, newest backup 1 hour old: waits, a minute at a time" "$(decide "$recent")" "sleep 60"
check "unreadable repository: no backup, waits to retry" "$(decide 'not json')" "sleep 300"
# pgBackRest can print a warning on stdout before the JSON (an invalid PGBACKREST_* variable does, whatever the log levels)
warn_first="P00   WARN: environment contains invalid option 'bogus-option'
$recent"
check "a WARN line before the JSON: read as the repository it describes (waits, nothing due)" "$(decide "$warn_first")" "sleep 60"
check "and the warning still reaches the log" "$(grep -c "pgbackrest info: P00   WARN: environment contains invalid option" "$work/out")" 1
rm -f "$work/ok"; decide "$recent" >/dev/null 2>&1
check "restarted with backups inside both intervals: healthy without a new backup" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" healthy
rm -f "$work/ok"; decide 'not json' >/dev/null 2>&1
check "restarted with an unreadable repository: not healthy" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" unhealthy

# the metrics (pg-backup-metrics.pl): written on every repository read, keeping the last readable backup times; each attempt recorded
metric() { grep -E "^$1( |\{)" "$work/metrics.prom" | sed -E 's/.* //' || :; }
rm -rf "$work/state"; t0=$(date +%s); decide 'not json' >/dev/null 2>&1
started="$(metric pipeshub_pg_backup_scheduler_start_timestamp_seconds)"
check "metrics: the scheduler's start is exported from the first loop, before any backup or readable repository" \
  "$([ -n "$started" ] && [ "$started" -ge "$t0" ] && [ "$started" -le "$(date +%s)" ] && echo now)" now
decide "$recent" >/dev/null 2>&1
check "metrics: a restart keeps the first start, so a first full backup that never completes keeps ageing" \
  "$(metric pipeshub_pg_backup_scheduler_start_timestamp_seconds)" "$started"
check "metrics: the repository is up" "$(metric pipeshub_pg_backup_repository_up)" 1
decide "$warn_first" >/dev/null 2>&1
check "metrics: a WARN line before the JSON keeps the repository up" "$(metric pipeshub_pg_backup_repository_up)" 1
check "metrics: the newest full backup's stop time" "$(metric 'pipeshub_pg_backup_last_success_timestamp_seconds\{stanza="",repo="",type="full"\}')" $((now - 86400))
decide 'not json' >/dev/null 2>&1
check "metrics: an unreadable repository is down" "$(metric pipeshub_pg_backup_repository_up)" 0
check "metrics: and the last readable backup times stay, so they keep ageing" \
  "$(metric 'pipeshub_pg_backup_last_success_timestamp_seconds\{stanza="",repo="",type="incr"\}')" $((now - 3600))
check "metrics: no attempt yet, no attempt metric" "$(metric pipeshub_pg_backup_last_attempt_success)" ""
STUB_BACKUPS=1 decide "[{\"status\":{\"code\":0},\"backup\":[$(backup full 691200),$(backup incr 3600)]}]" >/dev/null 2>&1
check "metrics: a backup that succeeded" "$(metric pipeshub_pg_backup_last_attempt_success)" 1
STUB_BACKUP_RC=1 decide "[{\"status\":{\"code\":0},\"backup\":[$(backup full 691200),$(backup incr 3600)]}]" >/dev/null 2>&1
check "metrics: a backup that failed" "$(metric pipeshub_pg_backup_last_attempt_success)" 0
rm -rf "$work/state"

# a drop noticed between two repository reads: the loop's next turn starts a full backup without reading the repository again, and the
# gap is in the metrics before that backup runs (it can run for hours, and its success clears the drop)
mkdir -p "$work/gaps"
STUB_SLEEPS=1 STUB_DROP_DURING_SLEEP="$work/gaps/000000010000000000000040" decide "$recent" >/dev/null 2>&1
check "a drop noticed while waiting: the next turn is a full backup" "$(grep -m1 backup "$work/log")" "backup --type=full --no-expire-auto"
check "and the gap is in the metrics before that backup starts" \
  "$(grep -E '^pipeshub_pg_backup_wal_gap_segments ' "$work/log.metrics-during" | sed 's/.* //' || :)" 1
rm -rf "$work/gaps" "$work/holds"

# WAL dropped by the queue cap: unhealthy at once, says so, and a full backup now even though one is not due. A drop file is named by
# the dropped segment and holds the commit time of its first transaction (empty when it has none). Backups never expire by themselves.
gaps="$work/gaps"; holds="$work/holds"; mkdir -p "$gaps"
open_gap() { [ -n "$(ls -A "$gaps")" ] && echo open || echo closed; }
hold_of() { cat "$holds/$1" 2>/dev/null; }
expired() { grep -qx expire "$work/log" && echo expired || echo kept; }
touch "$work/ok"; : > "$gaps/000000010000000000000015"; echo "2026-11-15 10:01:02.123456 UTC" > "$gaps/000000010000000000000016"
check "dropped WAL: a full backup at once" "$(decide "$recent")" "backup --type=full --no-expire-auto"
check "dropped WAL: unhealthy" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" unhealthy
check "dropped WAL: the gap's hold names the newest backup before its first drop" "$(hold_of 000000010000000000000015)" "incr-3600"
check "dropped WAL: the log names the first and last dropped segment" \
  "$(grep -c 'dropped WAL segments 000000010000000000000015 to 000000010000000000000016 (noticed' "$work/out")" 1
# recovery cannot cross the earliest missing segment (15), so a commit time read only from a later one (16) is not the gap's start
check "the gap starts at the earliest dropped segment even when only a later one has a commit time" \
  "$(grep -c 'cannot reach past segment 000000010000000000000015 until the next full backup: from segment 000000010000000000000015 (no commit time known), so the last reachable point is before 000000010000000000000015' "$work/out")" 1
check "and the later segment's commit is not given as the gap's start" "$(grep -c '10:01:02' "$work/out")" 0
check "the log says how to restore up to the gap, with the gap's own backup" "$(grep -c 'scripts/pg-restore.sh latest --set incr-3600;' "$work/out")" 1
check "and how to release this gap's hold" "$(grep -c 'scripts/pipeshub-compose.sh exec pg-backup pg-backup.sh clear-gap-hold 000000010000000000000015' "$work/out")" 1
echo "2026-11-15 09:59:00.000001 UTC" > "$gaps/000000010000000000000015"; decide "$recent" >/dev/null 2>&1
check "with a commit in the earliest dropped segment: that commit is the first one lost" \
  "$(grep -c 'cannot reach past the start of segment 000000010000000000000015 until the next full backup: its first transaction, committed at 2026-11-15 09:59:00.000001 UTC, is the first one lost' "$work/out")" 1
# the full backup succeeds: the drops it found before it started are covered, healthy again, nothing expired (the gap is held)
rm -f "$work/ok"
STUB_BACKUPS=1 decide "$recent" >/dev/null 2>&1
check "a full backup after the drops closes the gap" "$(open_gap)" closed
check "and the service is healthy again" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" healthy
check "the gap metric clears only after the covering full backup" \
  "$(grep -E '^pipeshub_pg_backup_wal_gap_segments ' "$work/log.metrics-during" | sed 's/.* //' || :):$(metric pipeshub_pg_backup_wal_gap_segments)" "2:0"
check "and nothing is expired while the gap is held" "$(expired)" kept
check "the hold outlives the gap" "$(hold_of 000000010000000000000015)" "incr-3600"
# a second outage while the first gap is held gets its own hold and its own restore point in the report
: > "$gaps/000000010000000000000050"
info2="[{\"status\":{\"code\":0},\"backup\":[$(backup full 86400),$(backup incr 3600),$(backup full 60)]}]"
decide "$info2" >/dev/null 2>&1
check "a second gap gets its own hold, with its own backup" "$(hold_of 000000010000000000000050)" "full-60"
check "the first gap's hold is unchanged" "$(hold_of 000000010000000000000015)" "incr-3600"
check "the report gives the second gap's backup" "$(grep -c 'scripts/pg-restore.sh latest --set full-60;' "$work/out")" 1
rm -f "${gaps:?}"/*
# WAL dropped again while a full backup ran: that drop is not removed, still unhealthy, nothing expired
: > "$gaps/000000010000000000000020"
port="$(perl -MIO::Socket::INET -e 'print IO::Socket::INET->new(Listen => 1, LocalPort => 0)->sockport')"
PG_BACKUP_METRICS_PORT="$port" STUB_SCRAPE_PORT="$port" STUB_BACKUPS=1 STUB_DROP_DURING_BACKUP="$gaps/000000010000000000000021" \
  decide "$info2" >/dev/null 2>&1
check "a drop during a running backup is served at once, not only after the backup" \
  "$(grep -E '^pipeshub_pg_backup_wal_gap_segments ' "$work/log.scrape-during.1" 2>/dev/null | sed 's/.* //' | tr -d '\r' || :)" 2
check "the metrics server stops with the scheduler" "$( (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && echo running || echo stopped)" stopped
check "a drop during the full backup keeps the gap open" "$(ls "$gaps")" "000000010000000000000021"
check "and the service unhealthy" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" unhealthy
check "and the backup's expiry is skipped, checked after the backup" "$(expired)" kept
rm -f "${gaps:?}"/*
rm -rf "${holds:?}"
# no drop and no hold after a backup: expire runs, on its own
STUB_BACKUPS=1 decide "[{\"status\":{\"code\":0},\"backup\":[$(backup full 691200),$(backup incr 3600)]}]" >/dev/null 2>&1
check "with no gap held, the backup is followed by expire" "$(expired)" expired
# the repository cannot be read at all: the gap-closing backup keeps the drops, since no hold could be recorded
: > "$gaps/000000010000000000000060"
STUB_INFO_FAIL=1 STUB_BACKUPS=1 decide "$recent" >/dev/null 2>&1
check "no recorded hold: the drops stay after the gap-closing backup" "$(ls "$gaps")" "000000010000000000000060"
check "and nothing is expired" "$(expired)" kept
# info answers, but the stanza is in error (archive unreadable) with an empty list: not "none", the hold stays pending
STUB_BACKUPS=1 decide '[{"status":{"code":1,"message":"missing stanza path"},"backup":[]}]' >/dev/null 2>&1
check "a stanza in error does not record \"none\": the hold stays pending and the drops stay" "$(hold_of 000000010000000000000060):$(ls "$gaps")" ":000000010000000000000060"
# readable after the backup: the hold forms then, and the drops are cleared
STUB_INFO_FAIL_UNTIL_BACKUP=1 STUB_BACKUPS=1 decide "$recent" >/dev/null 2>&1
check "a hold forms at the gap-closing backup when the repository was unreadable before" "$(hold_of 000000010000000000000060)" "incr-3600"
check "and only then are the drops cleared" "$(open_gap)" closed
rm -rf "${holds:?}"

# clearing holds: refused while drops are uncovered; under the lock; expire first, holds removed only after it succeeded
clear_hold() { : > "$work/log"; env PATH="$work:$PATH" PG_WAL_GAP_DIR="$gaps" PG_WAL_GAP_HOLDS="$holds" PG_BACKUP_LOCK="$work/lock" \
  PG_BACKUP_LOCK_WAIT_SECS=1 STUB_LOG="$work/log" "$@" bash "$here/postgres/pg-backup.sh" clear-gap-hold ${SEG:-} >/dev/null 2>&1; echo "$? $(cat "$work/log")"; }
mkdir -p "$holds"; echo incr-3600 > "$holds/000000010000000000000015"; echo full-60 > "$holds/000000010000000000000050"
: > "$gaps/000000010000000000000030"
check "clear-gap-hold is refused while a drop is uncovered" "$(clear_hold)" "1 "
rm -f "${gaps:?}"/*
check "a failed expire keeps every hold" "$(clear_hold STUB_EXPIRE_RC=1)" "1 expire"
check "both holds are still there" "$(ls "$holds" | wc -l | tr -d ' ')" 2
check "releasing one gap of two removes it without expiring (the other still needs every backup)" "$(SEG=000000010000000000000015 clear_hold)" "0 "
check "the other gap stays held" "$(ls "$holds")" "000000010000000000000050"
( exec 9>"$work/lock"; flock 9; /bin/sleep 3 ) & /bin/sleep 0.5
check "clear-gap-hold waits for the scheduler's lock and gives up if a backup holds it" "$(clear_hold)" "1 "
wait
# a drop written while clear-gap-hold waits for the lock is seen: the check runs under the lock
( exec 9>"$work/lock"; flock 9; /bin/sleep 1; : > "$gaps/000000010000000000000070"; /bin/sleep 1 ) & /bin/sleep 0.3
check "a drop written while clear-gap-hold waits for the lock refuses the release" "$(clear_hold PG_BACKUP_LOCK_WAIT_SECS=10)" "1 "
wait
check "and the hold stays" "$(ls "$holds")" "000000010000000000000050"
rm -f "${gaps:?}"/*
check "the last hold: expire, then release" "$(clear_hold)" "0 expire"
check "no hold is left" "$(ls -A "$holds" | wc -l | tr -d ' ')" 0
check "without a hold or drops, backups are followed by expire" \
  "$(STUB_BACKUPS=1 decide "[{\"status\":{\"code\":0},\"backup\":[$(backup full 691200),$(backup incr 3600)]}]" >/dev/null 2>&1; expired)" expired
# a hold nobody clears is released after PG_BACKUP_GAP_HOLD_DAYS (default 14), loudly, and expiry resumes
mkdir -p "$holds"; echo incr-3600 > "$holds/000000010000000000000015"; touch -d '-13 days' "$holds/000000010000000000000015"
STUB_BACKUPS=1 decide "[{\"status\":{\"code\":0},\"backup\":[$(backup full 691200),$(backup incr 3600)]}]" >/dev/null 2>&1
check "a 13-day-old hold stays and nothing expires" "$(expired)" kept
touch -d '-15 days' "$holds/000000010000000000000015"
STUB_BACKUPS=1 decide "[{\"status\":{\"code\":0},\"backup\":[$(backup full 691200),$(backup incr 3600)]}]" >/dev/null 2>&1
check "a 15-day-old hold is released and the backup is followed by expire again" "$(expired)" expired
check "the release is logged loudly" "$(grep -c 'RELEASING THE GAP HOLD on gap 000000010000000000000015 (backup incr-3600) after PG_BACKUP_GAP_HOLD_DAYS=14' "$work/out")" 1
check "and the hold is gone" "$(ls -A "$holds" | wc -l | tr -d ' ')" 0
echo incr-3600 > "$holds/000000010000000000000015"; touch -d '-3 days' "$holds/000000010000000000000015"
PG_BACKUP_GAP_HOLD_DAYS=2 decide "$recent" >/dev/null 2>&1
check "PG_BACKUP_GAP_HOLD_DAYS sets the cap" "$(ls -A "$holds" | wc -l | tr -d ' ')" 0
rm -rf "${holds:?}"
rm -f "${gaps:?}"/*

# pg_waldump stub: the first transaction of the segment, as pg_waldump --rmgr=Transaction prints it (empty with STUB_NO_COMMIT; hangs
# with STUB_WALDUMP_HANG); its arguments go to $STUB_WALDUMP_ARGS
cat > "$work/pg_waldump" <<'EOF'
#!/bin/sh
echo "$*" > "${STUB_WALDUMP_ARGS:-/dev/null}"
[ -n "${STUB_WALDUMP_HANG:-}" ] && exec /bin/sleep 60   # not the stub sleep on PATH
[ -n "${STUB_NO_COMMIT:-}" ] && exit 0
echo "rmgr: Transaction len (rec/tot):     98/    98, tx:          3, lsn: 0/010D26E0, prev 0/010D1570, desc: COMMIT 2026-11-15 18:35:41.608992 UTC; relcache init file inval dbid 1 tsid 1663"
echo "rmgr: Transaction len (rec/tot):     34/    34, tx:          4, lsn: 0/010D7858, prev 0/010D6B78, desc: COMMIT 2026-11-15 18:35:42.000001 UTC"
EOF
chmod +x "$work/pg_waldump"
push() { # [env assignments...] -> archive-push output; the gap directory says whether a drop was marked
  rm -rf "${gaps:?}"
  env PATH="$work:$PATH" PG_WAL_GAP_DIR="$gaps" "$@" sh "$here/postgres/pgbackrest-archive-push.sh" pg_wal/000000010000000000000001 2>&1
}
marked() { [ -n "$(ls -A "$gaps" 2>/dev/null)" ] && echo marked || echo none; }
drop="WARN: dropped WAL file '000000010000000000000001' because archive queue exceeded 4GiB"
check "archive queue capped at 4GiB by default" "$(push | head -1)" "queue max: 4GiB"
check "a cap from pgbackrest.env is kept" "$(push PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX=20GiB | head -1)" "queue max: 20GiB"
check "an empty cap means no cap" "$(push PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX= | head -1)" "queue max: unset"
push >/dev/null
check "a pushed segment marks no gap" "$(marked)" none
push STUB_PUSH_OUT="$drop" >/dev/null; rc=$?
check "a dropped segment marks the gap" "$(marked)" marked
check "and is reported archived, as pgBackRest reports it" "$rc" 0
check "the mark is named by the dropped segment and holds its first commit time, in a form a restore accepts" \
  "$(ls "$gaps"):$(cat "$gaps"/* 2>/dev/null)" "000000010000000000000001:2026-11-15 18:35:41.608992+00"
push STUB_PUSH_OUT="$drop" STUB_WALDUMP_ARGS="$work/waldump.args" >/dev/null
check "pg_waldump reads only the dropped segment (endseg = startseg)" "$(cat "$work/waldump.args")" \
  "--rmgr=Transaction pg_wal/000000010000000000000001 pg_wal/000000010000000000000001"
t0=$(date +%s); push STUB_PUSH_OUT="$drop" STUB_WALDUMP_HANG=1 >/dev/null; took=$(( $(date +%s) - t0 ))
check "a pg_waldump that hangs is cut off, the drop still marked" "$([ "$took" -lt 30 ] && marked)" marked
push STUB_PUSH_OUT="$drop" STUB_NO_COMMIT=1 >/dev/null
check "a dropped segment without a commit is still marked, with no time" "$(ls "$gaps"):$(cat "$gaps"/* 2>/dev/null)" "000000010000000000000001:"
push STUB_PUSH_OUT="$drop" PGBACKREST_LOG_LEVEL_CONSOLE=off PGBACKREST_LOG_LEVEL_STDERR=off >/dev/null
check "a drop is marked with the console and stderr log levels off" "$(marked)" marked
check "and still reaches the server log" "$(push STUB_PUSH_OUT="$drop" PGBACKREST_LOG_LEVEL_CONSOLE=off PGBACKREST_LOG_LEVEL_STDERR=off | grep -c 'dropped WAL file')" 1
env PATH="$work:$PATH" PG_WAL_GAP_DIR="$gaps" STUB_PUSH_OUT="WARN: dropped WAL file '000000010000000000000002' because archive queue exceeded 4GiB" \
  sh "$here/postgres/pgbackrest-archive-push.sh" pg_wal/000000010000000000000002 >/dev/null 2>&1
check "each dropped segment adds its own mark" "$(ls "$gaps" | tr '\n' ' ')" "000000010000000000000001 000000010000000000000002 "
set +e; push STUB_PUSH_RC=82 >/dev/null; rc=$?; set -e
check "a failed push fails the archive_command" "$rc" 82
# scripts/pg-restore.sh finds the installer's compose files: a stand-in docker prints the COMPOSE_FILE it was given and fails
mkdir -p "$work/install/scripts"; cp "$here/scripts/pg-restore.sh" "$here/scripts/pipeshub-compose.sh" "$work/install/scripts/"
printf '#!/bin/sh\necho "docker called" >&2; echo "COMPOSE_FILE=${COMPOSE_FILE:-}" >&2; exit 1\n' > "$work/docker"; chmod +x "$work/docker"
restore_files() { (cd "$work/install" && env -u COMPOSE_FILE PATH="$work:$PATH" "$@" bash scripts/pg-restore.sh latest 2>&1 | sed -n 's/^COMPOSE_FILE=//p' | head -1); }
printf 'PIPESHUB_COMPOSE_OVERRIDES=docker-compose.postgres-docstore.yml:docker-compose.postgres-backup.yml\n' > "$work/install/.env"
check "pg-restore.sh takes the installer's compose files from .env" "$(restore_files)" \
  "docker-compose.yml:docker-compose.postgres-docstore.yml:docker-compose.postgres-backup.yml"
check "scripts/pipeshub-compose.sh, from a fresh shell, uses the installer's compose files too" \
  "$(cd "$work/install" && env -u COMPOSE_FILE PATH="$work:$PATH" bash scripts/pipeshub-compose.sh ps 2>&1 | sed -n 's/^COMPOSE_FILE=//p')" \
  "docker-compose.yml:docker-compose.postgres-docstore.yml:docker-compose.postgres-backup.yml"
check "a COMPOSE_FILE in the environment wins" "$(restore_files COMPOSE_FILE=a.yml:b.yml)" "a.yml:b.yml"
printf 'COMPOSE_FILE=x.yml\nPIPESHUB_COMPOSE_OVERRIDES=y.yml\n' > "$work/install/.env"
check "a COMPOSE_FILE in .env is left to Compose" "$(restore_files)" ""
printf 'COMPOSE_PROJECT_NAME=pipeshub-ai\n' > "$work/install/.env"
check "a .env with neither key: pg-restore.sh goes on to Compose" \
  "$(cd "$work/install" && env -u COMPOSE_FILE PATH="$work:$PATH" bash scripts/pg-restore.sh latest 2>&1 | grep -c 'docker called')" 1
exit $fail
