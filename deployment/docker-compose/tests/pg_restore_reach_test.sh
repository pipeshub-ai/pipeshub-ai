#!/usr/bin/env bash
# No Docker: scripts/pg-restore-reach.pl against a stand-in repository. pgbackrest serves `info`, `repo-ls` and `repo-get` from a
# directory, and pg_waldump prints the text a segment file starts with (each is a sparse 16 MiB file, the segment size).
set -euo pipefail
cd "$(dirname "$0")/.."
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
cat > "$work/bin/pgbackrest" <<'EOF'
#!/bin/sh
case "$*" in
  *" info") cat "$STUB/info.json" ;;
  *repo-ls*) [ -z "${STUB_LS_FAIL:-}" ] || exit 1
    printf '{'; sep=; for f in $(cd "$STUB/repo/archive/pipeshub/17-1" && find . -type f | sed 's|^\./||'); do printf '%s"%s":{"type":"file"}' "$sep" "$f"; sep=,; done; printf '}' ;;
  *repo-get*) for a; do p="$a"; done; cat "$STUB/repo/$p" ;;
  *) exit 1 ;;
esac
EOF
# shellcheck disable=SC2016 # expands when the stand-in runs
printf '#!/bin/sh\necho "$1" >> "$STUB/waldump.log"; tr -d "\\000" < "$1"\n' > "$work/bin/pg_waldump"
chmod +x "$work/bin/pgbackrest" "$work/bin/pg_waldump"

# repo: segment numbers on timeline 1, each holding "lsn commit-time" lines (commit time "-" for a record without a commit)
repo() {
  rm -rf "$work/s"; mkdir -p "$work/s/repo/archive/pipeshub/17-1/0000000100000000"
  local seg
  for spec in "$@"; do
    seg="$(printf '0000000100000000%08X' "${spec%%:*}")"
    f="$work/s/repo/archive/pipeshub/17-1/0000000100000000/$seg-$(printf '%040d' 0)"
    for rec in $(tr ',' ' ' <<<"${spec#*:}"); do
      lsn="${rec%@*}"; t="${rec#*@}"
      if [ "$t" = - ]; then echo "rmgr: XLOG        len (rec/tot): 30/30, tx: 0, lsn: $lsn, prev 0/0, desc: SWITCH"
      else echo "rmgr: Transaction len (rec/tot): 34/34, tx: 750, lsn: $lsn, prev 0/0, desc: COMMIT 2026-11-25 ${t} UTC"; fi
    done > "$f"
    truncate -s 16M "$f"
  done
  # one full backup that ends in segment 1
  cat > "$work/s/info.json" <<'EOF'
[{"name":"pipeshub","repo":[{"key":1}],"archive":[{"id":"17-1","database":{"id":1,"repo-key":1}}],
  "backup":[{"label":"20261125-100000F","type":"full","timestamp":{"start":1795600800,"stop":1795600805},
             "lsn":{"start":"0/1000028","stop":"0/1000100"},"database":{"id":1,"repo-key":1},
             "archive":{"start":"000000010000000000000001","stop":"000000010000000000000001"}}]}]
EOF
}
reach() { # type target -> sets rc, out
  rc=0; : > "$work/s/waldump.log"
  out="$(STUB="$work/s" PATH="$work/bin:$PATH" PGBACKREST_STANZA=pipeshub perl scripts/pg-restore-reach.pl "$1" "$2" "" 2>&1)" || rc=$?
}

repo '1:0/1000200@10:00:10.000000' '2:0/2000100@10:05:00.000000' '3:0/3000100@10:10:00.000000'
reach time '2026-11-25 10:09:59+00'
check "a time before the last archived commit is reachable" "$rc $out" "0 "
reach time '2026-11-25 10:10:00+00'
check "a time exactly at the last commit is refused (recovery stops only at a later commit)" "$rc" 4
reach time '2026-11-25 10:30:00+00'
check "a time after the last archived commit is refused, naming that commit and the backup" \
  "$rc $(grep -c 'its last commit, at 2026-11-25 10:10:00.000000+00: restore it with: scripts/pg-restore.sh latest --set 20261125-100000F$' <<<"$out")" "4 1"

# a gap: segment 3 was dropped, 4 is archived
repo '1:0/1000200@10:00:10.000000' '2:0/2000100@10:05:00.000000' '4:0/4000100@10:15:00.000000'
reach time '2026-11-25 10:12:00+00'
check "a time past a WAL gap is refused, naming the missing segment" \
  "$rc $(grep -c 'segment 000000010000000000000003 is not in the repository' <<<"$out")" "4 1"
reach lsn 0/4000000
check "an LSN past the gap is refused" "$rc" 4
reach lsn 0/2000100
check "an LSN before the gap is reachable" "$rc" 0

# the last segment holds no commit (a restore point and a switch): an LSN target needs none, so only that segment is read
repo '1:0/1000200@10:00:10.000000' '2:0/2000100@10:05:00.000000' '3:0/3000100@-'
reach lsn 0/3000100
check "an LSN target reads only the last segment" "$rc $(wc -l < "$work/s/waldump.log")" "0 1"

# a repository that cannot be listed: unknown, not missing
repo '1:0/1000200@10:00:10.000000'
STUB_LS_FAIL=1 reach time '2026-11-25 10:30:00+00'
check "a listing that fails warns and lets the restore go ahead" \
  "$rc $(grep -c 'warning: could not list the archive of repository 1' <<<"$out")" "0 1"
exit $fail
