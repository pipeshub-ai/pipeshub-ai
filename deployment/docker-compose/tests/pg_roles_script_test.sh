#!/usr/bin/env bash
# postgres/pg-roles.sh with a stand-in psql and verifier script (no Docker): if any one of the nine verifiers cannot be computed, or comes out
# empty, the script fails before it sends anything to the server, so no role is created or changed with an empty password; with all nine it
# sends them, and the verifier file is written last and only then. The extra vetted-owner list is validated the same way.
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

# The vetted owners of the extra migration modules (extra-vetted-schemas): the names reach the server as one variable, and a missing file or a
# bad name stops the script before anything is sent, so a registered module is never left without its owner role.
printf '# comment\nalpha\n  beta_2   # trailing comment\n\n' >"$work/extra-ok"
run env "PG_EXTRA_VETTED_FILE=$work/extra-ok"
check "the schemas of the extra file are sent as one variable" "$code $(grep -c -- '-v extra=alpha,beta_2 ' "$work/psql.log")" "0 1"
run env
shipped="$(sed -e 's/#.*//' -e 's/[[:space:]]//g' postgres/extra-vetted-schemas | grep -v '^$' | paste -sd, - || true)"
check "the shipped extra-vetted-schemas file is the one the script reads by default" "$(grep -o -- '-v extra=[^ ]*' "$work/psql.log")" "-v extra=$shipped"
for bad in 'Bad' '1x' 'a;b' "a'b" 'a-b' 'x y' $'a\ta b'; do
  printf '%s\n' "$bad" >"$work/extra-bad"
  run env "PG_EXTRA_VETTED_FILE=$work/extra-bad"
  check "the name '$bad' stops the script" "$code" 1
  check "and nothing reached the server for '$bad'" "$(wc -c <"$work/psql.log" | tr -d ' ')" 0
done
printf 'alpha\nbeta\nalpha\n' >"$work/extra-dup"
run env "PG_EXTRA_VETTED_FILE=$work/extra-dup"
check "a name listed twice stops the script" "$code" 1
check "and nothing reached the server for the duplicate" "$(wc -c <"$work/psql.log" | tr -d ' ')" 0
check "and the message names the duplicate" "$(grep -c 'alpha is listed twice' "$work/out.txt")" 1
printf '  alpha  \n\tbeta\t# c\r\n' >"$work/extra-trim"
run env "PG_EXTRA_VETTED_FILE=$work/extra-trim"
check "spaces and tabs around a name are trimmed" "$code $(grep -c -- '-v extra=alpha,beta ' "$work/psql.log")" "0 1"
run env "PG_EXTRA_VETTED_FILE=$work/no-such-file"
check "a missing extra file stops the script" "$code" 1
check "and nothing reached the server for the missing file" "$(wc -c <"$work/psql.log" | tr -d ' ')" 0
exit $fail
