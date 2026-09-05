#!/usr/bin/env bash
# ==============================================================================
# Run every test suite that needs no external service, and report one summary.
# ==============================================================================
# The point is a single command that answers "is this tree sound?" without
# waiting on CI. Everything here runs against local files only: no Docker, no
# network, no cloud credentials, no running PipesHub.
#
#   scripts/verify.sh              # everything available
#   scripts/verify.sh --list       # what would run, and why anything is skipped
#   scripts/verify.sh shell python # only the named groups
#
# Groups: shell, python, frontend, node
#
# A suite that cannot run on this machine is reported as SKIP with the reason
# rather than silently passing — a green result from a suite that never
# executed is worse than a red one.
#
# Exit status is 0 only when every suite that ran passed.
# ==============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || { echo "cannot enter $ROOT" >&2; exit 2; }

BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; RESET=""
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
  GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
fi

LIST_ONLY=false
# Not named GROUPS: bash keeps the caller's supplementary group ids in that
# variable, so assigning to it is silently ignored and every suite is skipped.
SELECTED=()
for arg in "$@"; do
  case "$arg" in
    --list) LIST_ONLY=true ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    shell|python|frontend|node) SELECTED+=("$arg") ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done
[[ ${#SELECTED[@]} -eq 0 ]] && SELECTED=(shell python frontend node)

wants() { local g="$1"; local x; for x in "${SELECTED[@]}"; do [[ "$x" == "$g" ]] && return 0; done; return 1; }

RESULTS=()
FAILED=0
RAN=0

record() { RESULTS+=("$1|$2|$3"); }

# run <group> <label> <requirement-check> <command...>
run() {
  local group="$1" label="$2" precheck="$3"; shift 3
  wants "$group" || return 0

  local reason
  if ! reason="$(eval "$precheck" 2>&1)"; then
    record "SKIP" "$label" "${reason:-not available here}"
    return 0
  fi

  if $LIST_ONLY; then
    record "WOULD RUN" "$label" ""
    return 0
  fi

  printf '  %s… ' "$label"
  local out start elapsed
  start=$SECONDS
  if out="$("$@" 2>&1)"; then
    elapsed=$((SECONDS - start))
    printf '%sok%s %s(%ds)%s\n' "$GREEN" "$RESET" "$DIM" "$elapsed" "$RESET"
    record "PASS" "$label" "${elapsed}s"
  else
    elapsed=$((SECONDS - start))
    printf '%sFAILED%s %s(%ds)%s\n' "$RED" "$RESET" "$DIM" "$elapsed" "$RESET"
    record "FAIL" "$label" "${elapsed}s"
    printf '%s\n' "$out" | tail -25 | sed 's/^/      /'
    FAILED=$((FAILED + 1))
  fi
  RAN=$((RAN + 1))
}

have() { command -v "$1" >/dev/null 2>&1 || { echo "$1 not installed"; return 1; }; }
exists() { [[ -e "$1" ]] || { echo "$1 not present in this tree"; return 1; }; }

printf '\n%sVerifying %s%s\n\n' "$BOLD" "$ROOT" "$RESET"

# ── shell ────────────────────────────────────────────────────────────────────
run shell "installer unit tests" \
  'exists deployment/docker-compose/tests/installer_test.sh' \
  bash deployment/docker-compose/tests/installer_test.sh

run shell "release tooling tests" \
  'exists deployment/docker-compose/tests/release_tooling_test.sh' \
  bash deployment/docker-compose/tests/release_tooling_test.sh

run shell "omnigent helper tests" \
  'exists integrations/omnigent/tests/scripts_test.sh' \
  bash integrations/omnigent/tests/scripts_test.sh

# ── python ───────────────────────────────────────────────────────────────────
PY="${PYTHON:-python3}"
run python "python unit tests" \
  "exists backend/python/tests/unit && have $PY" \
  bash -c "cd backend/python && $PY -m pytest tests/unit -q -p no:warnings"

run python "golden agent evals" \
  "exists backend/python/tests/evals && have $PY" \
  bash -c "cd backend/python && $PY -m pytest tests/evals -q -p no:warnings"

run python "python integration (offline subset)" \
  "exists backend/python/tests/integration && have $PY" \
  bash -c "cd backend/python && $PY -m pytest tests/integration \
      --ignore=tests/integration/redis_cluster \
      --ignore=tests/integration/test_model_reasoning_effort_e2e.py \
      -q -p no:warnings --timeout=300"

# ── frontend ─────────────────────────────────────────────────────────────────
run frontend "frontend unit tests (vitest)" \
  'exists frontend/node_modules' \
  bash -c "cd frontend && npm run --silent test:unit"

run frontend "electron local-sync tests" \
  'exists frontend/node_modules' \
  bash -c "cd frontend && npm run --silent test:electron:local-sync"

# ── node ─────────────────────────────────────────────────────────────────────
run node "node backend tests (mocha)" \
  'exists backend/nodejs/apps/node_modules' \
  bash -c "cd backend/nodejs/apps && npm run --silent test"

# ── summary ──────────────────────────────────────────────────────────────────
printf '\n%s%-34s %s%s\n' "$BOLD" "SUITE" "RESULT" "$RESET"
printf '%s\n' "$(printf '─%.0s' {1..60})"
for row in "${RESULTS[@]}"; do
  IFS='|' read -r status label detail <<<"$row"
  case "$status" in
    PASS)      printf '  %-32s %spass%s %s%s%s\n'  "$label" "$GREEN"  "$RESET" "$DIM" "$detail" "$RESET" ;;
    FAIL)      printf '  %-32s %sFAIL%s %s%s%s\n'  "$label" "$RED"    "$RESET" "$DIM" "$detail" "$RESET" ;;
    SKIP)      printf '  %-32s %sskip%s %s%s%s\n'  "$label" "$YELLOW" "$RESET" "$DIM" "$detail" "$RESET" ;;
    "WOULD RUN") printf '  %-32s would run\n' "$label" ;;
  esac
done
printf '\n'

$LIST_ONLY && exit 0

if [[ "$RAN" -eq 0 ]]; then
  printf '%sNothing ran.%s Install dependencies, or check --list for why each suite was skipped.\n' "$YELLOW" "$RESET"
  exit 1
fi
if [[ "$FAILED" -gt 0 ]]; then
  printf '%s%d of %d suite(s) failed.%s\n' "$RED" "$FAILED" "$RAN" "$RESET"
  exit 1
fi
printf '%sAll %d suite(s) passed.%s\n' "$GREEN" "$RAN" "$RESET"
