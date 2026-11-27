#!/usr/bin/env bash
# ==============================================================================
# Tests for the Compose project guard of pg_fault_soak.sh — no Docker, no network.
# ==============================================================================
# The soak's cleanup runs `docker compose down -v`, which removes the project's
# volumes. The guard before it must refuse a project that already exists and must
# also refuse when it cannot ask Docker: a failed query that read as "no such
# project" would let the cleanup remove a kept run's volumes.
#
# Each case runs the real script against a stub docker that records its calls,
# answers the two guard queries as the case says, and fails every other command,
# so the script stops right after the guard.
#
# Run: bash deployment/docker-compose/tests/pg_fault_soak_guard_test.sh
# ==============================================================================
set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pg_fault_soak.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail=0

# case: ps-fails | volume-fails | exists | empty
stub() {
  cat > "$TMP/docker" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$TMP/calls"
case "\$1 \$2" in
  "ps -aq") [ "$1" = ps-fails ] && exit 1; [ "$1" = exists ] && echo 0123abcd; exit 0 ;;
  "volume ls") [ "$1" = volume-fails ] && exit 1; exit 0 ;;
esac
exit 1
EOF
  chmod +x "$TMP/docker"
}

check() {  # name, case, want_past_guard (0/1)
  stub "$2"; : > "$TMP/calls"
  DOCKER="$TMP/docker" PROJECT=kept-run OUT="$TMP/out.jsonl" bash "$SCRIPT" > "$TMP/out" 2>&1
  local past downs
  past=$(grep -c '^Compose project:' "$TMP/out")
  downs=$(grep -c 'compose.* down' "$TMP/calls")
  if [ "$past" = "$3" ] && { [ "$3" = 1 ] || [ "$downs" = 0 ]; }; then
    echo "ok   $1"
  else
    echo "FAIL $1 (past guard: $past, want $3; down -v calls: $downs)"; sed 's/^/     /' "$TMP/out"; fail=1
  fi
}

bash -n "$SCRIPT" && echo "ok   pg_fault_soak.sh parses" || { echo "FAIL pg_fault_soak.sh does not parse"; fail=1; }
check "a failed container query stops before the cleanup is armed" ps-fails 0
check "a failed volume query stops before the cleanup is armed" volume-fails 0
check "a project that already has containers is refused" exists 0
check "a new project gets past the guard" empty 1

exit $fail
