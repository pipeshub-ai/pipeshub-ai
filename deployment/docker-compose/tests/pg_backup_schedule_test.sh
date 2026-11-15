#!/usr/bin/env bash
# No Docker: postgres/pg-backup.sh picks the backup type from the ages of the backups in the repository (pgbackrest info), with
# pgbackrest and sleep stubbed: the first decision of the loop is recorded, then the stubbed sleep ends the script.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }

cat > "$work/pgbackrest" <<'EOF'
#!/bin/sh
case "$*" in
  *info*) cat "$STUB_INFO" ;;
  *backup*) echo "$*" >> "$STUB_LOG"; kill -TERM "$PPID" ;;
esac
EOF
printf '#!/bin/sh\necho "sleep $1" >> "$STUB_LOG"; kill -TERM "$PPID"\n' > "$work/sleep"
chmod +x "$work/pgbackrest" "$work/sleep"

decide() { # info JSON -> what the loop did first
  printf '%s' "$1" > "$work/info.json"; : > "$work/log"
  PATH="$work:$PATH" PG_BACKUP_OK_FILE="$work/ok" STUB_INFO="$work/info.json" STUB_LOG="$work/log" PG_BACKUP_FULL_DAYS="${FULL_DAYS:-7}" PG_BACKUP_INCR_HOURS=6 \
    timeout 10 bash "$here/postgres/pg-backup.sh" >/dev/null 2>&1 || true
  head -1 "$work/log"
}
now=$(date +%s)
backup() { printf '{"type":"%s","timestamp":{"start":%d,"stop":%d}}' "$1" $((now - $2)) $((now - $2)); }

check "no backup yet: a full one" "$(decide '[{"backup":[]}]')" "backup --type=full"
check "no backup yet, full backups every 10000 days: still a full one first" "$(FULL_DAYS=10000 decide '[{"backup":[]}]')" "backup --type=full"
check "newest full 8 days old: a full one" "$(decide "[{\"backup\":[$(backup full 691200),$(backup incr 3600)]}]")" "backup --type=full"
check "full 1 day, newest backup 7 hours old: an incremental one" \
  "$(decide "[{\"backup\":[$(backup full 86400),$(backup incr 25200)]}]")" "backup --type=incr"
waited="$(decide "[{\"backup\":[$(backup full 86400),$(backup incr 3600)]}]")"
case "$waited" in "sleep 1799"[89]|"sleep 18000") waited="sleep 18000" ;; esac   # a clock tick between the two reads
check "full 1 day, newest backup 1 hour old: waits the rest of the 6 hours" "$waited" "sleep 18000"
check "unreadable repository: no backup, waits to retry" "$(decide 'not json')" "sleep 300"
exit $fail
