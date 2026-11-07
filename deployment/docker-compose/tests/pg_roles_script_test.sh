#!/usr/bin/env bash
# postgres/pg-roles.sh with a stand-in psql and verifier script (no Docker): if any one of the nine verifiers cannot be computed, or comes out
# empty, the script fails before it sends anything to the server, so no role is created or changed with an empty password; with all nine it
# sends them, and the verifier file is written last and only then.
set -euo pipefail
cd "$(dirname "$0")/.."
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/auth"
printf '#!/bin/sh\necho "$@" >>"%s/psql.log"\n# the role SQL arrives on stdin; the userlist query (-c) prints rows\ncase " $* " in *" -c "*) echo "pipeshub_node_login" ;; *) cat >/dev/null ;; esac\nexit 0\n' "$work" >"$work/bin/psql"
chmod +x "$work/bin/psql"
# the stand-in verifier: fails (or prints nothing) for the variable named in VERIFIER_BREAK / VERIFIER_EMPTY
cat >"$work/verifier" <<'STUB'
#!/bin/sh
[ "$1" = "${VERIFIER_BREAK:-}" ] && { echo "boom" >&2; exit 1; }
[ "$1" = "${VERIFIER_EMPTY:-}" ] && exit 0
echo "SCRAM-SHA-256\$4096:salt\$key:key-$1"
STUB
chmod +x "$work/verifier"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2', wanted '$3'"; fail=1; fi; }
run() { : >"$work/psql.log"; rm -f "$work/auth/userlist.txt"
  PATH="$work/bin:$PATH" PG_SCRAM_VERIFIER="$work/verifier" PG_AUTH_DIR="$work/auth" "$@" bash postgres/pg-roles.sh >"$work/out.txt" 2>&1 && code=0 || code=$?; }

for var in PG_NODE_PASSWORD PG_CONNECTORS_PASSWORD PG_INDEXING_PASSWORD PG_QUERY_PASSWORD PG_JOBS_PASSWORD PG_MIGRATE_PASSWORD PG_BACKUP_PASSWORD PG_KV_PASSWORD; do
  run env "VERIFIER_BREAK=$var"
  check "a failing verifier for $var stops the script" "$code" 1
  check "and nothing reached the server" "$(wc -c <"$work/psql.log" | tr -d ' ')" 0
  check "and no verifier file was written" "$([ -e "$work/auth/userlist.txt" ] && echo written || echo absent)" absent
done
run env "VERIFIER_EMPTY=PG_JOBS_PASSWORD"
check "an empty verifier stops the script" "$code" 1
check "and nothing reached the server for it" "$(wc -c <"$work/psql.log" | tr -d ' ')" 0
check "and the message names the variable" "$(grep -c 'PG_JOBS_PASSWORD' "$work/out.txt")" 1

run env
check "with all nine verifiers the script succeeds" "$code" 0
check "and sends them to the server as variables" "$(grep -o -- '-v [a-z]*=SCRAM' "$work/psql.log" | head -1 | wc -l | tr -d ' ')" 1
check "and writes the verifier file" "$([ -e "$work/auth/userlist.txt" ] && echo written || echo absent)" written
exit $fail
