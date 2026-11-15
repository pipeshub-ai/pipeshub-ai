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
  *info*) cat "$STUB_INFO" ;;
  *backup*) # the first STUB_BACKUPS backups (default none) return; the next one ends the script
    echo "$*" >> "$STUB_LOG"; n=$(grep -c backup "$STUB_LOG")
    if [ -e "$PG_BACKUP_OK_FILE" ]; then echo "healthy during backup" >> "$STUB_LOG.health"; else echo "unhealthy during backup" >> "$STUB_LOG.health"; fi
    if [ "$n" -gt "${STUB_BACKUPS:-0}" ]; then kill -TERM "$PPID"; exit 1; fi
    [ -z "${STUB_DROP_DURING_BACKUP:-}" ] || : > "$STUB_DROP_DURING_BACKUP" ;;
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
printf '#!/bin/sh\necho "sleep $1" >> "$STUB_LOG"; kill -TERM "$PPID"\n' > "$work/sleep"
chmod +x "$work/pgbackrest" "$work/sleep"

decide() { # info JSON -> what the loop did first
  printf '%s' "$1" > "$work/info.json"; : > "$work/log"
  PATH="$work:$PATH" PG_BACKUP_OK_FILE="$work/ok" PG_WAL_GAP_DIR="$work/gaps" STUB_INFO="$work/info.json" STUB_LOG="$work/log" \
    PG_BACKUP_FULL_DAYS="${FULL_DAYS:-7}" PG_BACKUP_INCR_HOURS=6 timeout 10 bash "$here/postgres/pg-backup.sh" >"$work/out" 2>&1 || true
  head -1 "$work/log"
}
now=$(date +%s)
backup() { printf '{"type":"%s","timestamp":{"start":%d,"stop":%d}}' "$1" $((now - $2)) $((now - $2)); }
recent="[{\"backup\":[$(backup full 86400),$(backup incr 3600)]}]"

check "no backup yet: a full one" "$(decide '[{"backup":[]}]')" "backup --type=full"
check "no backup yet: healthy while the first full backup runs" "$(tail -1 "$work/log.health")" "healthy during backup"
check "no backup yet, full backups every 10000 days: still a full one first" "$(FULL_DAYS=10000 decide '[{"backup":[]}]')" "backup --type=full"
rm -f "$work/ok"
check "newest full 8 days old: a full one" "$(decide "[{\"backup\":[$(backup full 691200),$(backup incr 3600)]}]")" "backup --type=full"
check "a later full backup does not mark itself healthy before it succeeds" "$(tail -1 "$work/log.health")" "unhealthy during backup"
check "full 1 day, newest backup 7 hours old: an incremental one" \
  "$(decide "[{\"backup\":[$(backup full 86400),$(backup incr 25200)]}]")" "backup --type=incr"
check "full 1 day, newest backup 1 hour old: waits, a minute at a time" "$(decide "$recent")" "sleep 60"
check "unreadable repository: no backup, waits to retry" "$(decide 'not json')" "sleep 300"
rm -f "$work/ok"; decide "$recent" >/dev/null 2>&1
check "restarted with backups inside both intervals: healthy without a new backup" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" healthy
rm -f "$work/ok"; decide 'not json' >/dev/null 2>&1
check "restarted with an unreadable repository: not healthy" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" unhealthy

# WAL dropped by the queue cap: unhealthy at once, says so, and a full backup now even though one is not due. A drop file is named by
# the dropped segment and holds the commit time of its first transaction (empty when it has none).
gaps="$work/gaps"; mkdir -p "$gaps"
open_gap() { [ -n "$(ls -A "$gaps")" ] && echo open || echo closed; }
touch "$work/ok"; : > "$gaps/000000010000000000000015"; echo "2026-11-15 10:01:02.123456 UTC" > "$gaps/000000010000000000000016"
check "dropped WAL: a full backup at once" "$(decide "$recent")" "backup --type=full"
check "dropped WAL: unhealthy" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" unhealthy
check "dropped WAL: the log names the first and last dropped segment" \
  "$(grep -c 'dropped WAL segments 000000010000000000000015 to 000000010000000000000016 (noticed' "$work/out")" 1
check "and the first transaction a restore can no longer reach, with the gap until the next full backup" \
  "$(grep -c 'cannot reach the transaction committed at 2026-11-15 10:01:02.123456 UTC or anything after it until the next full' "$work/out")" 1
: > "$gaps/000000010000000000000016"; decide "$recent" >/dev/null 2>&1
check "no commit in the dropped segments: the gap is given by segment, and may start before it was noticed" \
  "$(grep -c 'cannot reach past segment 000000010000000000000015, which may hold transactions from before the time it was noticed' "$work/out")" 1
# the full backup succeeds: the drops it found before it started are covered, healthy again
rm -f "$work/ok"
STUB_BACKUPS=1 decide "$recent" >/dev/null 2>&1
check "a full backup after the drops closes the gap" "$(open_gap)" closed
check "and the service is healthy again" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" healthy
# WAL dropped again while that full backup ran: that drop is not removed, still unhealthy
: > "$gaps/000000010000000000000020"
STUB_BACKUPS=1 STUB_DROP_DURING_BACKUP="$gaps/000000010000000000000021" decide "$recent" >/dev/null 2>&1
check "a drop during the full backup keeps the gap open" "$(ls "$gaps")" "000000010000000000000021"
check "and the service unhealthy" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" unhealthy
rm -f "$gaps"/*

# pg_waldump stub: the first transaction of the segment, as pg_waldump --rmgr=Transaction prints it (empty with STUB_NO_COMMIT)
cat > "$work/pg_waldump" <<'EOF'
#!/bin/sh
[ -n "${STUB_NO_COMMIT:-}" ] && exit 0
echo "rmgr: Transaction len (rec/tot):     98/    98, tx:          3, lsn: 0/010D26E0, prev 0/010D1570, desc: COMMIT 2026-11-15 18:35:41.608992 UTC; relcache init file inval dbid 1 tsid 1663"
echo "rmgr: Transaction len (rec/tot):     34/    34, tx:          4, lsn: 0/010D7858, prev 0/010D6B78, desc: COMMIT 2026-11-15 18:35:42.000001 UTC"
EOF
chmod +x "$work/pg_waldump"
push() { # [env assignments...] -> archive-push output; the gap directory says whether a drop was marked
  rm -rf "$gaps"
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
check "the mark is named by the dropped segment and holds its first commit time" \
  "$(ls "$gaps"):$(cat "$gaps"/* 2>/dev/null)" "000000010000000000000001:2026-11-15 18:35:41.608992 UTC"
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
mkdir -p "$work/install/scripts"; cp "$here/scripts/pg-restore.sh" "$work/install/scripts/"
printf '#!/bin/sh\necho "COMPOSE_FILE=${COMPOSE_FILE:-}" >&2; exit 1\n' > "$work/docker"; chmod +x "$work/docker"
restore_files() { (cd "$work/install" && env -u COMPOSE_FILE PATH="$work:$PATH" "$@" bash scripts/pg-restore.sh latest 2>&1 | sed -n 's/^COMPOSE_FILE=//p' | head -1); }
printf 'PIPESHUB_COMPOSE_OVERRIDES=docker-compose.postgres-docstore.yml:docker-compose.postgres-backup.yml\n' > "$work/install/.env"
check "pg-restore.sh takes the installer's compose files from .env" "$(restore_files)" \
  "docker-compose.yml:docker-compose.postgres-docstore.yml:docker-compose.postgres-backup.yml"
check "a COMPOSE_FILE in the environment wins" "$(restore_files COMPOSE_FILE=a.yml:b.yml)" "a.yml:b.yml"
printf 'COMPOSE_FILE=x.yml\nPIPESHUB_COMPOSE_OVERRIDES=y.yml\n' > "$work/install/.env"
check "a COMPOSE_FILE in .env is left to Compose" "$(restore_files)" ""
exit $fail
