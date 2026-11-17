#!/usr/bin/env bash
# ==============================================================================
# Tests for the document-store move scripts (scripts/migrate-to-postgres.sh,
# scripts/rollback-to-mongo.sh, scripts/docstore-migrate.sh) — pure bash, no
# Docker, no network. `docker` is a stub on PATH that records every call and
# answers for the migration tool, so these check what the scripts decide:
#   - the order of the steps per path (open the import login, stop the app,
#     preflight, copy / follow / rollback, close the login);
#   - re-running after a failed or interrupted run is safe and closes the login;
#   - no password reaches the output, a command line, or the SQL sent to psql;
#   - how the install is found (env file, project name, override files);
#   - usage errors exit 2 before docker is called.
# The scripts run under $BASH_UNDER_TEST (default: bash on PATH). Set it to a bash 3.2 binary to check them as macOS runs them;
# scripts/verify.sh does when BASH32 names one.
#
# Run: bash deployment/docker-compose/tests/docstore_move_scripts_test.sh
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MIGRATE="$COMPOSE_DIR/scripts/migrate-to-postgres.sh"
ROLLBACK="$COMPOSE_DIR/scripts/rollback-to-mongo.sh"
TOOL="$COMPOSE_DIR/scripts/docstore-migrate.sh"
BASH_UNDER_TEST="${BASH_UNDER_TEST:-bash}"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

PASS_FILE="$TMP_ROOT/.pass"; FAIL_FILE="$TMP_ROOT/.fail"
: >"$PASS_FILE"; : >"$FAIL_FILE"
pass() { printf "  ok   - %s\n" "$1"; echo x >>"$PASS_FILE"; }
fail() { printf "  FAIL - %s\n" "$1"; echo "$1" >>"$FAIL_FILE"; }
check() { # check "desc" actual expected_substring
  if [[ "$2" == *"$3"* ]]; then pass "$1"; else
    fail "$1"; printf "         expected to contain: %s\n         got: %s\n" "$3" "$2"
  fi
}
check_not() { # check_not "desc" actual unexpected_substring
  if [[ "$2" != *"$3"* ]]; then pass "$1"; else
    fail "$1"; printf "         should not contain: %s\n         got: %s\n" "$3" "$2"
  fi
}
check_eq() { # check_eq "desc" actual expected
  if [[ "$2" == "$3" ]]; then pass "$1"; else
    fail "$1"; printf "         expected: %s\n         got:      %s\n" "$3" "$2"
  fi
}

# ── the docker stub ───────────────────────────────────────────────────────────
# Logs one line per call to $CALLS: a step name the tests compare, then the full argv. psql's stdin goes to $SQL.
# STUB_FAIL=<tool command> makes that tool command exit 1; the tool's URLs are checked against the env file's passwords.
BIN="$TMP_ROOT/bin"; mkdir -p "$BIN"
cat >"$BIN/docker" <<'STUB'
#!/usr/bin/env bash
args=" $* "
step=other
case "$args" in
  *" exec "*scram-verifier.pl*) step=verifier; echo 'SCRAM-SHA-256$4096:c2FsdA==$c3RvcmVk:c2VydmVy' ;;
  *" exec "*psql*) sql=$(cat); printf '%s\n' "$sql" >>"$SQL"
                   case "$sql" in *NOLOGIN*) step=login-close ;; *LOGIN*) step=login-open ;; *) step=psql ;; esac ;;
  *" stop "*) step="stop:${*: -1}" ;;
  *docstore-migrate.js*)
    tool=${args#*docstore-migrate.js }; tool=${tool% }; step="tool:$tool"
    # the service's own entrypoint starts the application whatever the arguments are: only the image entrypoint runs them
    [[ "$args" == *" run "*" --entrypoint /usr/local/bin/docker-entrypoint.sh pipeshub-ai node /app/backend/dist/scripts/docstore-migrate.js "* ]] \
      || step="APP_STARTED_INSTEAD:$tool"
    # the URLs reach the container through the environment, built from the env file
    [[ "${PG_MIGRATE_URL:-}" == "postgresql://pipeshub_migrate:${EXPECT_MIGRATE_PW}@postgres:5432/pipeshub" ]] || step="$step:BAD_MIGRATE_URL"
    [[ "${PG_NODE_URL:-}" == "postgresql://pipeshub_node_login:${EXPECT_NODE_PW}@pgbouncer:6432/pipeshub" ]] || step="$step:BAD_NODE_URL"
    [[ "${PG_IMPORT_URL:-}" == postgresql://pipeshub_import:*@postgres:5432/pipeshub ]] || step="$step:BAD_IMPORT_URL"
    [[ "${MIGRATE_SCHEMA_COMMAND:-}" == none ]] || step="$step:BAD_SCHEMA_COMMAND"
    printf '%s\n' "${PG_IMPORT_URL:-}" >>"$IMPORT_URLS"
    echo '{"ok": true}'
    if [[ "$tool" == "follow run" && -n "${STUB_FOLLOW_SIGNALS:-}" ]]; then
      # a follower that runs until a signal reaches it, and records which one
      trap 'echo INT >>"$STUB_FOLLOW_SIGNALS"; exit 0' INT
      trap 'echo TERM >>"$STUB_FOLLOW_SIGNALS"; exit 0' TERM
      echo ready >"$STUB_FOLLOW_SIGNALS.ready"
      for _ in $(seq 1 100); do sleep 0.1; done
      echo none >>"$STUB_FOLLOW_SIGNALS"
    fi
    [[ -n "${STUB_FAIL:-}" && "$tool" == "$STUB_FAIL"* ]] && { echo "$step" >>"$CALLS"; echo "  argv: $*" >>"$ARGV"; exit 1; } ;;
esac
echo "$step" >>"$CALLS"
echo "  argv: $*" >>"$ARGV"
exit 0
STUB
chmod +x "$BIN/docker"

# BSD sed takes the word after -i as the backup suffix, so GNU's suffix-less `sed -i SCRIPT FILE` fails there (macOS)
BSD_BIN="$TMP_ROOT/bsdbin"; mkdir -p "$BSD_BIN"
REAL_SED="$(command -v sed)"
cat >"$BSD_BIN/sed" <<STUB
#!/usr/bin/env bash
for a in "\$@"; do [[ "\$a" == -i ]] && { echo "sed: 1: \"\$2\": invalid command code" >&2; exit 1; }; done
exec "$REAL_SED" "\$@"
STUB
chmod +x "$BSD_BIN/sed"

MIGRATE_PW=mig_$RANDOM$RANDOM$RANDOM
NODE_PW=node_$RANDOM$RANDOM$RANDOM

# new_install [extra env lines...]: a fresh env file and empty logs
new_install() {
  INST="$TMP_ROOT/inst$RANDOM$RANDOM"; mkdir -p "$INST"
  ENVF="$INST/.env"
  { echo "COMPOSE_PROFILES=graph-neo4j,postgres,postgres-migrate"
    echo "PG_MIGRATE_PASSWORD=$MIGRATE_PW"
    echo "PG_NODE_PASSWORD=$NODE_PW"
    for l in "$@"; do echo "$l"; done
  } >"$ENVF"
  chmod 600 "$ENVF"
  export CALLS="$INST/calls" ARGV="$INST/argv" SQL="$INST/sql" IMPORT_URLS="$INST/urls"
  : >"$CALLS"; : >"$ARGV"; : >"$SQL"; : >"$IMPORT_URLS"
}
# run_script <script> [args...]: runs it against $ENVF with the stub; sets OUT (stdout+stderr) and RC
run_script() {
  OUT="$(PATH="$BIN:$PATH" PIPESHUB_ENV_FILE="$ENVF" EXPECT_MIGRATE_PW="$MIGRATE_PW" EXPECT_NODE_PW="$NODE_PW" \
         "$BASH_UNDER_TEST" "$@" 2>&1)"; RC=$?
}
steps() { paste -sd' ' "$CALLS"; }
import_pw() { sed -n 's/^PG_IMPORT_PASSWORD=//p' "$ENVF"; }
# every place a password could show up: the output, the docker command lines, the SQL sent to psql
no_secret_shown() { # no_secret_shown "desc" password
  local all; all="$OUT$(cat "$ARGV" "$SQL")"
  if [[ -n "$2" && "$all" != *"$2"* ]]; then pass "$1"; else fail "$1"; fi
}

echo "== Syntax =="
for f in "$MIGRATE" "$ROLLBACK" "$TOOL"; do
  if [[ -f "$f" ]] && "$BASH_UNDER_TEST" -n "$f" 2>/dev/null; then pass "$(basename "$f") parses"; else fail "$(basename "$f") parses"; fi
done

echo
echo "== usage: a missing or unknown choice exits 2 before docker is called =="
new_install
run_script "$MIGRATE"
check_eq "migrate with no path exits 2" "$RC" 2
check "and shows the offline/online choice" "$OUT" "--offline"
check "and the online choice" "$OUT" "--online"
check_eq "no docker call" "$(steps)" ""
run_script "$MIGRATE" --offline --online
check_eq "both paths at once exit 2" "$RC" 2
run_script "$MIGRATE" --flip
check_eq "an unknown flag exits 2" "$RC" 2
run_script "$ROLLBACK" --reverse --online
check_eq "rollback --reverse --online exits 2" "$RC" 2
run_script "$ROLLBACK" --bogus
check_eq "rollback with an unknown flag exits 2" "$RC" 2
run_script "$MIGRATE" --help
check_eq "--help exits 0" "$RC" 0
check "--help names the steps" "$OUT" "import login"
check_eq "--help calls no docker" "$(steps)" ""
run_script "$TOOL"
check_eq "docstore-migrate.sh with no command exits 2" "$RC" 2

echo
echo "== offline: open the login, stop the app, preflight, copy, close the login =="
new_install
run_script "$MIGRATE" --offline
check_eq "exits 0" "$RC" 0
check_eq "steps in order" "$(steps)" \
  "verifier login-open stop:pipeshub-ai tool:preflight tool:migrate --offline login-close"
check "the login is opened with a SCRAM verifier, not the password" "$(cat "$SQL")" "ALTER ROLE pipeshub_import LOGIN PASSWORD 'SCRAM-SHA-256\$"
pw="$(sed -n 's#^postgresql://pipeshub_import:\(.*\)@postgres:5432/pipeshub$#\1#p' "$IMPORT_URLS" | head -1)"
check_not "the tool gets a non-empty import password" "x${pw}x" "xx"
no_secret_shown "the import password is never shown" "$pw"
no_secret_shown "the migrate password is never shown" "$MIGRATE_PW"
no_secret_shown "the node password is never shown" "$NODE_PW"
check_eq "the import password is gone from the env file afterwards" "$(import_pw)" ""
check "the other env lines are kept" "$(cat "$ENVF")" "PG_MIGRATE_PASSWORD=$MIGRATE_PW"
check_eq "the env file stays private" "$(stat -c %a "$ENVF")" 600
check "it says how to start the app on PostgreSQL" "$OUT" "docker-compose.postgres-docstore.yml"

echo
echo "== offline: re-running is safe =="
run_script "$MIGRATE" --offline
check_eq "a second run exits like the tool (0 here)" "$RC" 0
check_eq "and does the same steps" "$(steps | sed 's/.*login-close verifier/verifier/')" \
  "verifier login-open stop:pipeshub-ai tool:preflight tool:migrate --offline login-close"

new_install
STUB_FAIL="migrate --offline" run_script "$MIGRATE" --offline
check_eq "a failed copy exits 1" "$RC" 1
check_eq "and still closes the login" "$(steps)" \
  "verifier login-open stop:pipeshub-ai tool:preflight tool:migrate --offline login-close"
check_eq "and removes the import password" "$(import_pw)" ""

new_install
STUB_FAIL="preflight" run_script "$MIGRATE" --offline
check_eq "a red preflight exits 1" "$RC" 1
check_eq "and stops before the copy, closing the login" "$(steps)" \
  "verifier login-open stop:pipeshub-ai tool:preflight login-close"

new_install "PG_IMPORT_PASSWORD=left_by_a_killed_run_42"
run_script "$MIGRATE" --offline
check_eq "a run interrupted before closing: the next run exits 0" "$RC" 0
check "and reuses the password it left" "$(cat "$IMPORT_URLS")" "pipeshub_import:left_by_a_killed_run_42@"
no_secret_shown "without showing it" "left_by_a_killed_run_42"
check_eq "and closes the login" "$(import_pw)" ""

echo
echo "== online: open the login, preflight, copy, follow; the login stays open while the move runs =="
new_install
run_script "$MIGRATE" --online
check_eq "exits 0 when the follower stops" "$RC" 0
check_eq "steps in order" "$(steps)" \
  "verifier login-open tool:preflight --allow-clients tool:migrate --online tool:follow run"
pw="$(import_pw)"
check_not "the import password stays in the env file for cutover and reverse" "x${pw}x" "xx"
no_secret_shown "and is never shown" "$pw"
check "it says how to close the login" "$OUT" "--close-login"

run_script "$MIGRATE" --online
check "a second run reuses the open login's password" "$(sort -u "$IMPORT_URLS" | wc -l | tr -d ' ')" 1

new_install
STUB_FAIL="migrate --online" run_script "$MIGRATE" --online
check_eq "a failed online copy exits 1" "$RC" 1
check_eq "and closes the login (nothing runs that needs it)" "$(steps)" \
  "verifier login-open tool:preflight --allow-clients tool:migrate --online login-close"

echo
echo "== --close-login and --reset =="
new_install "PG_IMPORT_PASSWORD=still_open_99"
run_script "$MIGRATE" --close-login
check_eq "--close-login exits 0" "$RC" 0
check_eq "and only closes the login" "$(steps)" "login-close"
check_eq "and removes the password" "$(import_pw)" ""
run_script "$MIGRATE" --close-login
check_eq "closing a closed login is fine" "$RC" 0

new_install
run_script "$MIGRATE" --reset
check_eq "--reset exits 0" "$RC" 0
check_eq "opens the login, empties the copy, closes the login" "$(steps)" \
  "verifier login-open tool:reset --yes login-close"

echo
echo "== rollback =="
new_install
run_script "$ROLLBACK"
check_eq "rollback exits 0" "$RC" 0
check_eq "opens the login, stops the app, rolls back, closes the login" "$(steps)" \
  "verifier login-open stop:pipeshub-ai tool:rollback login-close"
check "it says how to start the app on MongoDB" "$OUT" "PIPESHUB_COMPOSE_OVERRIDES"
new_install
run_script "$ROLLBACK" --reverse
check_eq "--reverse copies back with the app stopped" "$(steps)" \
  "verifier login-open stop:pipeshub-ai tool:rollback --reverse login-close"
new_install
run_script "$ROLLBACK" --online
check_eq "--online leaves the app running" "$(steps)" \
  "verifier login-open tool:rollback --online login-close"
new_install
STUB_FAIL="rollback" run_script "$ROLLBACK"
check_eq "a refused rollback exits 1" "$RC" 1
check "and closes the login" "$(steps)" "login-close"
no_secret_shown "rollback never shows the migrate password" "$MIGRATE_PW"

echo
echo "== docstore-migrate.sh: any tool command, the login left as it is =="
new_install "PG_IMPORT_PASSWORD=open_for_cutover_7"
run_script "$TOOL" cutover --dry-run --fence-armed --shadow-clean
check_eq "exits like the tool" "$RC" 0
check_eq "runs only the tool" "$(steps)" "tool:cutover --dry-run --fence-armed --shadow-clean"
check "with the open login's password" "$(cat "$IMPORT_URLS")" "pipeshub_import:open_for_cutover_7@"
no_secret_shown "without showing it" "open_for_cutover_7"
check_eq "and leaves it in the env file" "$(import_pw)" "open_for_cutover_7"

echo
echo "== the tool runs through the image entrypoint, never the service's (which starts the app) =="
new_install
run_script "$TOOL" status
check "--entrypoint is the image entrypoint, before the service" "$(cat "$ARGV")" \
  "--entrypoint /usr/local/bin/docker-entrypoint.sh pipeshub-ai node /app/backend/dist/scripts/docstore-migrate.js status"
check_eq "and the stub saw the tool run" "$(steps)" "tool:status"

echo
echo "== online: Ctrl+C reaches the follower, and the script still says how to close the login =="
new_install
SIGS="$INST/signals"
# Ctrl+C signals the whole foreground process group; job control gives the script its own group to signal
(
  set -m
  PATH="$BIN:$PATH" PIPESHUB_ENV_FILE="$ENVF" EXPECT_MIGRATE_PW="$MIGRATE_PW" EXPECT_NODE_PW="$NODE_PW" \
    STUB_FOLLOW_SIGNALS="$SIGS" "$BASH_UNDER_TEST" "$MIGRATE" --online >"$INST/out" 2>&1 &
  pid=$!
  for _ in $(seq 1 100); do [[ -f "$SIGS.ready" ]] && break; sleep 0.05; done
  kill -INT -- "-$pid" 2>/dev/null
  for _ in $(seq 1 100); do kill -0 "$pid" 2>/dev/null || break; sleep 0.05; done
  kill -KILL -- "-$pid" 2>/dev/null
  wait "$pid"; echo $? >"$INST/rc"
)
check_eq "the follower got SIGINT" "$(head -1 "$SIGS" 2>/dev/null)" INT
check_eq "the script exits 0 after the follower stops" "$(cat "$INST/rc")" 0
check "and says how to close the login" "$(cat "$INST/out")" "--close-login"
check_not "and does not close it" "$(steps)" "login-close"

echo
echo "== the env file is edited with portable commands, keeps its mode, and stays a symlink =="
new_install
OUT="$(PATH="$BSD_BIN:$BIN:$PATH" PIPESHUB_ENV_FILE="$ENVF" EXPECT_MIGRATE_PW="$MIGRATE_PW" EXPECT_NODE_PW="$NODE_PW" "$BASH_UNDER_TEST" "$MIGRATE" --offline 2>&1)"; RC=$?
check_eq "offline with BSD sed exits 0" "$RC" 0
check_eq "and opens and closes the login" "$(steps)" \
  "verifier login-open stop:pipeshub-ai tool:preflight tool:migrate --offline login-close"
check_eq "the password is removed with BSD sed" "$(import_pw)" ""
check_eq "the env file stays 0600" "$(stat -c %a "$ENVF")" 600
new_install
mv "$ENVF" "$INST/real.env"; ln -s real.env "$ENVF"
OUT="$(PATH="$BSD_BIN:$BIN:$PATH" PIPESHUB_ENV_FILE="$ENVF" EXPECT_MIGRATE_PW="$MIGRATE_PW" EXPECT_NODE_PW="$NODE_PW" "$BASH_UNDER_TEST" "$MIGRATE" --online 2>&1)"; RC=$?
check_eq "online with BSD sed exits 0" "$RC" 0
check "and stores the password through the symlink" "$(cat "$INST/real.env")" "PG_IMPORT_PASSWORD="
if [[ -L "$ENVF" ]]; then pass "a symlinked env file stays a symlink"; else fail "a symlinked env file stays a symlink"; fi
check_eq "the real file stays 0600" "$(stat -c %a "$INST/real.env")" 600
left="$(shopt -s nullglob; f=("$INST"/*.env.* "$INST"/.env.*); echo ${f[@]+"${f[*]}"})"
if [[ -z "$left" ]]; then pass "no temporary or backup file is left beside the env file"; else fail "no temporary or backup file is left beside the env file: $left"; fi

echo
echo "== which install: project name and override files =="
new_install
run_script "$TOOL" status
check "default project pipeshub-ai" "$(cat "$ARGV")" "-p pipeshub-ai"
check "reads the env file" "$(cat "$ARGV")" "--env-file $ENVF"
new_install "COMPOSE_PROJECT_NAME=fromenv" "PIPESHUB_COMPOSE_OVERRIDES=docker-compose.postgres-docstore.yml"
run_script "$TOOL" status
check "COMPOSE_PROJECT_NAME from the env file" "$(cat "$ARGV")" "-p fromenv"
check "the override files from the env file" "$(cat "$ARGV")" "-f docker-compose.yml -f docker-compose.postgres-docstore.yml"
OUT="$(PATH="$BIN:$PATH" PIPESHUB_ENV_FILE="$ENVF" PIPESHUB_PROJECT=wins EXPECT_MIGRATE_PW="$MIGRATE_PW" EXPECT_NODE_PW="$NODE_PW" "$BASH_UNDER_TEST" "$TOOL" status 2>&1)"
check "PIPESHUB_PROJECT wins" "$(tail -1 "$ARGV")" "-p wins"
new_install
rm -f "$ENVF"
run_script "$MIGRATE" --offline
check_eq "a missing env file exits 1" "$RC" 1
check_eq "before docker is called" "$(steps)" ""

echo
P=$(wc -l <"$PASS_FILE" | tr -d ' '); F=$(wc -l <"$FAIL_FILE" | tr -d ' ')
echo "passed: $P, failed: $F"
[[ "$F" -eq 0 ]] || { echo; echo "Failed:"; sed 's/^/  - /' "$FAIL_FILE"; exit 1; }
