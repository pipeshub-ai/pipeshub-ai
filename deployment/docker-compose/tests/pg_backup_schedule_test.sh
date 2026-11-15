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
  *backup*) echo "$*" >> "$STUB_LOG"; [ -z "${STUB_TOUCH_GAP:-}" ] || touch "$STUB_TOUCH_GAP"
            [ -n "${STUB_BACKUP_CONTINUE:-}" ] || kill -TERM "$PPID" ;;
  *archive-push*) echo "queue max: ${PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX-unset}"; printf '%s' "${STUB_PUSH_OUT:-}"; exit "${STUB_PUSH_RC:-0}" ;;
esac
EOF
printf '#!/bin/sh\necho "sleep $1" >> "$STUB_LOG"; kill -TERM "$PPID"\n' > "$work/sleep"
chmod +x "$work/pgbackrest" "$work/sleep"

decide() { # info JSON -> what the loop did first
  printf '%s' "$1" > "$work/info.json"; : > "$work/log"
  PATH="$work:$PATH" PG_BACKUP_OK_FILE="$work/ok" PG_WAL_GAP_FILE="$work/gap" STUB_INFO="$work/info.json" STUB_LOG="$work/log" \
    PG_BACKUP_FULL_DAYS="${FULL_DAYS:-7}" PG_BACKUP_INCR_HOURS=6 timeout 10 bash "$here/postgres/pg-backup.sh" >"$work/out" 2>&1 || true
  head -1 "$work/log"
}
now=$(date +%s)
backup() { printf '{"type":"%s","timestamp":{"start":%d,"stop":%d}}' "$1" $((now - $2)) $((now - $2)); }
recent="[{\"backup\":[$(backup full 86400),$(backup incr 3600)]}]"

check "no backup yet: a full one" "$(decide '[{"backup":[]}]')" "backup --type=full"
check "no backup yet, full backups every 10000 days: still a full one first" "$(FULL_DAYS=10000 decide '[{"backup":[]}]')" "backup --type=full"
check "newest full 8 days old: a full one" "$(decide "[{\"backup\":[$(backup full 691200),$(backup incr 3600)]}]")" "backup --type=full"
check "full 1 day, newest backup 7 hours old: an incremental one" \
  "$(decide "[{\"backup\":[$(backup full 86400),$(backup incr 25200)]}]")" "backup --type=incr"
check "full 1 day, newest backup 1 hour old: waits, a minute at a time" "$(decide "$recent")" "sleep 60"
check "unreadable repository: no backup, waits to retry" "$(decide 'not json')" "sleep 300"

# WAL dropped by the queue cap: unhealthy at once, says so, and a full backup now even though one is not due
touch "$work/ok" "$work/gap"
check "dropped WAL: a full backup at once" "$(decide "$recent")" "backup --type=full"
check "dropped WAL: unhealthy" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" unhealthy
check "dropped WAL: the log says restores have a gap until the next full backup" \
  "$(grep -c 'point-in-time restore has a gap from then until the next full backup' "$work/out")" 1
# the full backup succeeds: the gap is closed, healthy again
touch -d '-1 minute' "$work/gap"; rm -f "$work/ok"
STUB_BACKUP_CONTINUE=1 decide "$recent" >/dev/null 2>&1
check "a full backup after the drop closes the gap" "$([ -e "$work/gap" ] && echo open || echo closed)" closed
check "and the service is healthy again" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" healthy
# WAL dropped again while that full backup ran: still a gap, still unhealthy
touch -d '-1 minute' "$work/gap"
STUB_BACKUP_CONTINUE=1 STUB_TOUCH_GAP="$work/gap" decide "$recent" >/dev/null 2>&1
check "a drop during the full backup keeps the gap open" "$([ -e "$work/gap" ] && echo open || echo closed)" open
check "and the service unhealthy" "$([ -e "$work/ok" ] && echo healthy || echo unhealthy)" unhealthy
rm -f "$work/gap"

push() { # [env assignments...] -> archive-push output; the gap file says whether a drop was marked
  rm -f "$work/gap"
  env PATH="$work:$PATH" PG_WAL_GAP_FILE="$work/gap" "$@" sh "$here/postgres/pgbackrest-archive-push.sh" pg_wal/000000010000000000000001 2>&1
}
check "archive queue capped at 4GiB by default" "$(push | head -1)" "queue max: 4GiB"
check "a cap from pgbackrest.env is kept" "$(push PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX=20GiB | head -1)" "queue max: 20GiB"
check "an empty cap means no cap" "$(push PGBACKREST_ARCHIVE_PUSH_QUEUE_MAX= | head -1)" "queue max: unset"
push >/dev/null
check "a pushed segment marks no gap" "$([ -e "$work/gap" ] && echo marked || echo none)" none
push STUB_PUSH_OUT="WARN: dropped WAL file '000000010000000000000001' because archive queue exceeded 4GiB" >/dev/null; rc=$?
check "a dropped segment marks the gap" "$([ -e "$work/gap" ] && echo marked || echo none)" marked
first="$(cat "$work/gap")"; touch -d '-1 minute' "$work/gap"
env PATH="$work:$PATH" PG_WAL_GAP_FILE="$work/gap" STUB_PUSH_OUT="WARN: dropped WAL file '000000010000000000000002' because archive queue exceeded 4GiB" \
  sh "$here/postgres/pgbackrest-archive-push.sh" pg_wal/000000010000000000000002 >/dev/null 2>&1
check "a later drop keeps the time of the first one and moves the last one" \
  "$([ "$(cat "$work/gap")" = "$first" ] && [ "$(( $(date +%s) - $(stat -c %Y "$work/gap") ))" -lt 30 ] && echo yes)" yes
check "and is reported archived, as pgBackRest reports it" "$rc" 0
set +e; push STUB_PUSH_RC=82 >/dev/null; rc=$?; set -e
check "a failed push fails the archive_command" "$rc" 82
exit $fail
