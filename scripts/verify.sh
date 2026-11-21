#!/usr/bin/env bash
# ==============================================================================
# Run every test suite that needs no external service, and report one summary.
# ==============================================================================
# The point is a single command that answers "is this tree sound?" without
# waiting on CI. Everything here runs against local files only: no Docker, no
# network, no cloud credentials, no running PipesHub.
#
# It does not run integration-tests/, which needs the whole stack and shared
# cloud storage. It does run backend/python/tests/integration, which despite the
# directory name is mostly in-process: the suites there that do need a service
# skip themselves when it is absent, so on a bare machine they cost nothing.
#
#   scripts/verify.sh              # everything available
#   scripts/verify.sh --list       # what would run, and why anything is skipped
#   scripts/verify.sh shell python # only the named groups
#   PXH_VERIFY_E2E=1 scripts/verify.sh e2e   # opt in: the browser lane (needs Docker; ~25 min)
#
# Groups: shell, python, frontend, node, e2e. The e2e group is the one exception to "no Docker": it runs only when
# PXH_VERIFY_E2E=1, bringing up a throwaway stack (deployment/e2e/run.sh browser), running the harness and the
# authenticated Playwright projects on its scripted model, and tearing it down.
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
    shell|python|frontend|node|e2e) SELECTED+=("$arg") ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done
[[ ${#SELECTED[@]} -eq 0 ]] && SELECTED=(shell python frontend node e2e)

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

# An interpreter that cannot import pytest is not a runnable suite. Without this
# the suite is listed as runnable and then reports a test failure, which reads
# as a broken tree rather than a missing dependency.
has_pytest() {
  "$1" -c 'import pytest' >/dev/null 2>&1 \
    || { echo "$1 cannot import pytest (activate the venv, or set PYTHON=)"; return 1; }
  # The service-test command passes --timeout, which pytest rejects outright
  # without this plugin — that would be reported as a failing suite rather than
  # a missing prerequisite.
  "$1" -c 'import pytest_timeout' >/dev/null 2>&1 \
    || { echo "$1 lacks pytest-timeout (pip install pytest-timeout)"; return 1; }
}

# A suite that drives a real browser needs the browser build the installed
# Playwright version expects, not just the npm package.
has_playwright_chromium() {
  (cd frontend && node -e "
    const { chromium } = require('@playwright/test');
    process.exit(require('fs').existsSync(chromium.executablePath()) ? 0 : 1);
  ") >/dev/null 2>&1 \
    || { echo "Playwright Chromium not installed (cd frontend && npx playwright install chromium)"; return 1; }
}

printf '\n%sVerifying %s%s\n\n' "$BOLD" "$ROOT" "$RESET"

# ── shell ────────────────────────────────────────────────────────────────────
run shell "installer unit tests" \
  'exists deployment/docker-compose/tests/installer_test.sh' \
  bash deployment/docker-compose/tests/installer_test.sh

run shell "installer sandbox-plane tests" \
  'exists deployment/docker-compose/tests/sandbox_plane_test.sh' \
  bash deployment/docker-compose/tests/sandbox_plane_test.sh

run shell "installer offline bundle tests" \
  'exists deployment/docker-compose/tests/offline_bundle_test.sh' \
  bash deployment/docker-compose/tests/offline_bundle_test.sh

run shell "eks purge tests" \
  'exists deployment/helm/tests/purge_bucket_test.sh' \
  bash deployment/helm/tests/purge_bucket_test.sh

run shell "eks tool install tests" \
  'exists deployment/helm/tests/install_deps_test.sh' \
  bash deployment/helm/tests/install_deps_test.sh

run shell "eks install profile tests" \
  'exists deployment/helm/tests/aws_install_profile_test.sh' \
  bash deployment/helm/tests/aws_install_profile_test.sh

run shell "release tooling tests" \
  'exists deployment/docker-compose/tests/release_tooling_test.sh' \
  bash deployment/docker-compose/tests/release_tooling_test.sh

run shell "omnigent helper tests" \
  'exists integrations/omnigent/tests/scripts_test.sh' \
  bash integrations/omnigent/tests/scripts_test.sh

# ── python ───────────────────────────────────────────────────────────────────
PY="${PYTHON:-python3}"
# Standard library only (python3 -I): the guard's own tests, then what is not committed yet. CI runs it over a
# pull request's commits (.github/workflows/edition-guard.yml).
run python "edition guard" \
  'exists scripts/edition_guard.py && have python3' \
  bash -c "python3 -I -m unittest discover -s scripts -p 'test_edition_guard.py' && python3 -I scripts/edition_guard.py --worktree"

# R31-JOURNEY (ADR-TEST-01): every feasible feature x path x shape cell covered or tracked, every named test real;
# the owner-shape fixture held to its derived counts. Standard library only.
run python "journey registry coverage" \
  'exists scripts/journey_matrix.py && have python3' \
  bash -c "python3 -I -m unittest discover -s scripts -p 'test_journey_matrix.py' && python3 -I scripts/journey_matrix.py --check && python3 -I scripts/owner_shape.py --check"

run python "python unit tests" \
  "exists backend/python/tests/unit && have $PY && has_pytest $PY" \
  bash -c "cd backend/python && $PY -m pytest tests/unit -q --disable-warnings"

run python "cohesion and contract suites" \
  "exists backend/python/tests/cohesion && have $PY && has_pytest $PY" \
  bash -c "cd backend/python && $PY -m pytest tests/cohesion tests/contracts -m 'not docker and not slow' -q --disable-warnings"

run python "golden agent evals" \
  "exists backend/python/tests/evals && have $PY && has_pytest $PY" \
  bash -c "cd backend/python && $PY -m pytest tests/evals -q --disable-warnings"

run python "python service tests (no infra)" \
  "exists backend/python/tests/integration && have $PY && has_pytest $PY" \
  bash -c "cd backend/python && $PY -m pytest tests/integration \
      --ignore=tests/integration/redis_cluster \
      --ignore=tests/integration/skills/test_npm_import_live.py \
      -q --disable-warnings --timeout=300"

# ── frontend ─────────────────────────────────────────────────────────────────
run frontend "locale parity" \
  'have node' \
  node frontend/scripts/check-i18n-parity.mjs

run frontend "translation keys resolve" \
  'have node' \
  node frontend/scripts/check-i18n-keys.mjs

run frontend "i18n checker tests" \
  'have node' \
  node --test frontend/scripts/check-i18n-parity.test.mjs frontend/scripts/check-i18n-keys.test.mjs

run frontend "frontend unit tests (vitest)" \
  'exists frontend/node_modules && have npm' \
  bash -c "cd frontend && npm run --silent test:unit"

run frontend "text-fragment browser tests" \
  'exists frontend/node_modules/@playwright/test && have node && has_playwright_chromium' \
  bash -c "cd frontend && npm run --silent test:text-fragments"

run frontend "electron main-process tests" \
  'exists frontend/node_modules && have npm' \
  bash -c "cd frontend && npm run --silent test:electron"

# ── node ─────────────────────────────────────────────────────────────────────
run node "node backend tests (mocha)" \
  'exists backend/nodejs/apps/node_modules && have npm' \
  bash -c "cd backend/nodejs/apps && npm run --silent test"

# The harness and control-plane stores on PostgreSQL, with their own coverage floor (.c8rc.pg-harness.json). They
# start a throwaway PostgreSQL and PgBouncer from local binaries (no Docker), named by the PG_TEST_* variables; a
# passing run under its floor fails here, as it does in CI's pg-stores workflow (DOS-MONGO-FU-1: it slipped unseen).
has_pg_binaries() {
  local v
  for v in PG_TEST_BINS PG_TEST_PGBOUNCER PG_TEST_MIGRATE_PYTHON; do
    [[ -n "${!v:-}" ]] || { echo "set PG_TEST_BINS, PG_TEST_PGBOUNCER and PG_TEST_MIGRATE_PYTHON (local PostgreSQL 17 and PgBouncer)"; return 1; }
  done
}
# The operator CLI itest among them runs its migrations against a Redis KV store.
has_redis_it() { [[ -n "${REDIS_IT_URL:-}" ]] || { echo "set REDIS_IT_URL (a local Redis, e.g. redis://127.0.0.1:6379)"; return 1; }; }
run node "node postgresql stores (coverage floor)" \
  'exists backend/nodejs/apps/node_modules && have npm && has_pg_binaries && has_redis_it' \
  bash -c "cd backend/nodejs/apps && npm run --silent test:pg-harness"

# The MongoDB -> PostgreSQL move on a migrated PostgreSQL: table and column coverage, the copy and verify of every
# mapped collection (R44SMALL-FU-3: the coverage itest was red unseen because nothing ran it). The cases that need a
# MongoDB skip unless MIGRATE_TEST_MONGOD names a mongod binary.
run node "node docstore-migrate itests" \
  'exists backend/nodejs/apps/node_modules && have npm && has_pg_binaries' \
  bash -c "cd backend/nodejs/apps && npm run --silent test:docstore-migrate"

# ── e2e (opt-in) ─────────────────────────────────────────────────────────────
opted_in_e2e() {
  [[ "${PXH_VERIFY_E2E:-0}" == "1" ]] || { echo "opt-in: set PXH_VERIFY_E2E=1 (brings up a throwaway Docker stack)"; return 1; }
}
run e2e "browser lane (harness + authenticated)" \
  'opted_in_e2e && have docker && exists frontend/node_modules && exists backend/nodejs/apps/node_modules' \
  bash deployment/e2e/run.sh browser

# R31-JOURNEY: the journey gate tier on an own human-style stack (scripted model), torn down after.
run e2e "journey gate tier (human-style stack)" \
  'opted_in_e2e && have docker && exists deployment/e2e/journeys.py' \
  bash -c 'export E2E_RUN="vj$(date +%s)" E2E_PROJECT_PREFIX=pxh-vj- E2E_STACK=ST-1 E2E_HUMAN_DIR="${E2E_HUMAN_DIR:?the embedding model cache (hf/) of a human stack}"; state="${E2E_STATE_DIR:-$HOME/.cache/pxh-e2e}"; bash deployment/e2e/run.sh human-up && python3 deployment/e2e/journeys.py "$state/$E2E_RUN" --tier gate; status=$?; bash deployment/e2e/run.sh down "$E2E_RUN"; exit $status'

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
