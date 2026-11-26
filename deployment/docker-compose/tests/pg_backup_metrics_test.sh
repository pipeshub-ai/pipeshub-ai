#!/usr/bin/env bash
# No Docker: postgres/pg-backup-metrics.pl, the metrics of the `pg-backup` service. From a `pgbackrest --output=json info` fixture (the
# shape pgBackRest 2.60.0 prints: tests/fixtures/pgbackrest-info.json) it exports the newest backup of each type per repository, keeps the
# last readable times when the repository cannot be read, and serves the file over HTTP without letting a silent client block it.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"; srv=""; trap '[ -z "$srv" ] || kill "$srv" 2>/dev/null; rm -rf "$work"' EXIT
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
mkdir -p "$work/state" "$work/gaps"
write() { perl "$here/postgres/pg-backup-metrics.pl" write "$1" "$work/state" "$work/gaps" 604800 21600 "$work/m.prom"; }
metric() { awk -v n="$1" '$1 == n { print $2 }' "$work/m.prom"; }
ts() { metric "pipeshub_pg_backup_last_success_timestamp_seconds{stanza=\"pipeshub\",repo=\"$1\",type=\"$2\"}"; }

write "$here/tests/fixtures/pgbackrest-info.json"
check "readable stanza: repository up" "$(metric pipeshub_pg_backup_repository_up)" 1
check "newest full of repo 1 (two fulls)" "$(ts 1 full)" 1794103200
check "newest incr of repo 1 (two incrs)" "$(ts 1 incr)" 1794160860
check "a diff with page errors is still a finished backup" "$(ts 1 diff)" 1794139800
check "repo 2 is its own series" "$(ts 2 full)" 1793588400
check "no series for a type a repository lacks" "$(ts 2 incr)" ""
# ages as the alert rules compute them (time() - stop), at 2026-11-09 06:00 UTC
at=1794204000
check "age of repo 1's newest full: 28 h" "$(( at - $(ts 1 full) ))" 100800
check "age of repo 1's newest backup of any type: 12 h 1 min" "$(( at - $(ts 1 incr) ))" 43140
check "duration of the newest full" "$(metric 'pipeshub_pg_backup_last_duration_seconds{stanza="pipeshub",repo="1",type="full"}')" 7200
check "the full schedule" "$(metric 'pipeshub_pg_backup_schedule_seconds{type="full"}')" 604800
check "the incremental schedule" "$(metric 'pipeshub_pg_backup_schedule_seconds{type="incr"}')" 21600
check "no attempt yet: no attempt series" "$(metric pipeshub_pg_backup_last_attempt_success)" ""
check "no dropped WAL" "$(metric pipeshub_pg_backup_wal_gap_segments)" 0

# pgBackRest can print a warning on stdout before the JSON, whatever the log levels (an invalid PGBACKREST_* variable does, live
# with 2.60.0): the JSON document from its first line is still read
rm -f "$work/state/info.json"
{ echo "P00   WARN: environment contains invalid option 'bogus-option'"; cat "$here/tests/fixtures/pgbackrest-info.json"; } > "$work/warn.json"
write "$work/warn.json" 2>"$work/err"
check "a WARN line before the JSON: repository up" "$(metric pipeshub_pg_backup_repository_up)" 1
check "a WARN line before the JSON: the newest full is read" "$(ts 1 full)" 1794103200

# unreadable: an empty read (pgbackrest info failed), invalid JSON, a stanza in error; the last readable times stay
for bad in "" "not json" '[{"name":"pipeshub","status":{"code":1,"message":"missing stanza path"},"backup":[]}]'; do
  printf '%s' "$bad" > "$work/new.json"; write "$work/new.json"
  check "unreadable (${bad:0:12}...): repository down" "$(metric pipeshub_pg_backup_repository_up)" 0
  check "unreadable (${bad:0:12}...): the last readable full time stays" "$(ts 1 full)" 1794103200
done
printf 'not json' > "$work/new.json"; write "$work/new.json" 2>"$work/err"
check "text without JSON is logged as no JSON" "$(grep -c 'repository unreadable: pgbackrest info printed no JSON' "$work/err")" 1
printf '[{"name":' > "$work/new.json"; write "$work/new.json" 2>"$work/err"
check "broken JSON is logged as not JSON" "$(grep -c 'repository unreadable: not JSON' "$work/err")" 1
printf '[{"name":"pipeshub","status":{"code":1,"message":"missing stanza path"},"backup":[]}]' > "$work/new.json"; write "$work/new.json" 2>"$work/err"
check "a stanza in error is logged with its status" "$(grep -c 'repository unreadable: stanza pipeshub: status 1 (missing stanza path)' "$work/err")" 1
rm -f "$work/state/info.json"; printf '' > "$work/new.json"; write "$work/new.json"
check "never readable: down, and no backup series" "$(metric pipeshub_pg_backup_repository_up):$(grep -c last_success_timestamp_seconds\{ "$work/m.prom" || true)" "0:0"
printf '[{"name":"pipeshub","status":{"code":2,"message":"no valid backups"},"backup":[]}]' > "$work/new.json"; write "$work/new.json"
check "a new repository without backups is up" "$(metric pipeshub_pg_backup_repository_up)" 1

check "no scheduler start recorded: no start series" "$(metric pipeshub_pg_backup_scheduler_start_timestamp_seconds)" ""
echo 1793000000 > "$work/state/started"
echo "1794200000 0" > "$work/state/attempt"; : > "$work/gaps/000000010000000000000015"; : > "$work/gaps/000000010000000000000016"
write "$here/tests/fixtures/pgbackrest-info.json"
check "the last attempt failed" "$(metric pipeshub_pg_backup_last_attempt_success)" 0
check "when it ended" "$(metric pipeshub_pg_backup_last_attempt_timestamp_seconds)" 1794200000
check "two dropped WAL segments" "$(metric pipeshub_pg_backup_wal_gap_segments)" 2
check "the scheduler's first start" "$(metric pipeshub_pg_backup_scheduler_start_timestamp_seconds)" 1793000000
check "the file is the Prometheus text format: each series has HELP and TYPE" \
  "$(grep -c '^# TYPE' "$work/m.prom"):$(grep -c '^# HELP' "$work/m.prom")" "8:8"

# serve: a free port, the file at /metrics; a client that never sends a request is dropped after 5 s
port="$(perl -MIO::Socket::INET -e 'print IO::Socket::INET->new(Listen => 1, LocalPort => 0)->sockport')"
perl "$here/postgres/pg-backup-metrics.pl" serve "$port" "$work/m.prom" "$work/gaps" & srv=$!
get() { exec 3<>"/dev/tcp/127.0.0.1/$port"; printf 'GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n' >&3; cat <&3; exec 3<&-; }
for _ in $(seq 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done
check "serve: 200 with the file" "$(get | head -1 | tr -d '\r')" "HTTP/1.1 200 OK"
check "serve: the body is the file" "$(get | grep -c '^pipeshub_pg_backup_repository_up 1')" 1
: > "$work/gaps/000000010000000000000017"
check "serve: a drop since the file was written is counted at once" "$(get | grep -E '^pipeshub_pg_backup_wal_gap_segments ' | tr -d '\r')" "pipeshub_pg_backup_wal_gap_segments 3"
check "serve: the file itself is unchanged (the scheduler rewrites it)" "$(metric pipeshub_pg_backup_wal_gap_segments)" 2
exec 4<>"/dev/tcp/127.0.0.1/$port"   # silent client
t0=$(date +%s); out="$(get | head -1 | tr -d '\r')"; took=$(( $(date +%s) - t0 ))
exec 4<&-
check "serve: a silent client delays the next request by at most its 5 s" "$out:$([ "$took" -le 7 ] && echo bounded)" "HTTP/1.1 200 OK:bounded"
mv "$work/m.prom" "$work/m.prom.away"
check "serve: no file yet is a failed scrape (503), not an empty success" "$(get | head -1 | tr -d '\r')" "HTTP/1.1 503 Service Unavailable"
exit "$fail"
