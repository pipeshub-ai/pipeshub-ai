#!/usr/bin/env bash
# Run the test suites of the SDK checkouts side by side against one app.
#
# Usage: run_sdk_tests.sh <run name> <sdks dir> <reports dir> [language ...]
#
# <sdks dir> holds pipeshub-sdk-<language> checkouts. Each suite writes
# <reports dir>/<run name>-<language>.xml (JUnit) and a .log next to it.
# The suites share one app and one set of data; the tests are written to check
# only what they created, so they can run at the same time.
# Reads PIPESHUB_API_URL, PIPESHUB_TOKEN_URL, PIPESHUB_CLIENT_ID and
# PIPESHUB_CLIENT_SECRET from the environment. Exits non-zero if a suite failed.
set -uo pipefail

RUN_NAME=$1
SDKS_DIR=$(realpath "$2")
REPORTS_DIR=$(realpath -m "$3")
shift 3
LANGS=("$@")
[ ${#LANGS[@]} -gt 0 ] || LANGS=(typescript python go)

# A test that calls the LLM takes about 30 s and a lifecycle makes several calls.
TEST_TIMEOUT_SECONDS=${SDK_TEST_TIMEOUT_SECONDS:-600}

mkdir -p "$REPORTS_DIR"

run_typescript() {
  npm ci || return 1
  npm test -- --testTimeout=$((TEST_TIMEOUT_SECONDS * 1000))
  local status=$?
  cp .speakeasy/reports/tests.xml "$1" || return 1
  return $status
}

run_python() {
  python3 -m venv .venv-sdk-tests || return 1
  .venv-sdk-tests/bin/pip install --quiet -e . pytest pytest-asyncio pytest-xdist pytest-timeout || return 1
  .venv-sdk-tests/bin/pytest tests/ -n 4 --timeout="$TEST_TIMEOUT_SECONDS" --junitxml="$1"
}

run_go() {
  go install github.com/jstemmer/go-junit-report/v2@v2.1.0 || return 1
  go test -count=1 -timeout 60m -v ./tests/... 2>&1 \
    | "$(go env GOPATH)/bin/go-junit-report" -set-exit-code -iocopy -out "$1"
}

pids=()
for lang in "${LANGS[@]}"; do
  report="$REPORTS_DIR/$RUN_NAME-$lang.xml"
  rm -f "$report"
  ( cd "$SDKS_DIR/pipeshub-sdk-$lang" && "run_$lang" "$report" ) \
    > "$REPORTS_DIR/$RUN_NAME-$lang.log" 2>&1 &
  pids+=($!)
done

failed=()
for i in "${!LANGS[@]}"; do
  wait "${pids[$i]}" || failed+=("${LANGS[$i]}")
done

if [ ${#failed[@]} -gt 0 ]; then
  echo "$RUN_NAME run failed for: ${failed[*]}" >&2
  exit 1
fi
echo "$RUN_NAME run passed for: ${LANGS[*]}"
