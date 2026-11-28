#!/usr/bin/env bash
# ==============================================================================
# Tests for deployment/offline-env.sh (PIPESHUB_OFFLINE=1) and for how
# docker-entrypoint.sh uses it. Pure shell: no Docker, no network.
#
# Covers: every switch set when the flag is on; an operator's value kept; flag
# off/empty/unset changes nothing; one log line naming what was set; applying it
# twice is quiet; the entrypoint applies it on both start paths (as root, and as
# uid 1000); process_monitor.sh sources it for a start that bypassed the entrypoint.
#
# Run: bash deployment/docker-compose/tests/offline_env_test.sh
# ==============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT="$ROOT/deployment/offline-env.sh"
ENTRYPOINT="$ROOT/deployment/docker-entrypoint.sh"
SWITCHES="TELEMETRY_COLLECTION_FLAG=false HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 LITELLM_LOCAL_MODEL_COST_MAP=True DO_NOT_TRACK=1"

FAILS=0
pass() { printf "  ok   - %s\n" "$1"; }
fail() { printf "  FAIL - %s\n" "$1"; FAILS=$((FAILS + 1)); }
check() { if eval "$2"; then pass "$1"; else fail "$1"; fi; }

# Prints "NAME=value" lines for the switches, as seen after sourcing the script
# in a clean environment plus the given assignments. Stderr goes to $ERR.
ERR=$(mktemp)
trap 'rm -f "$ERR"' EXIT
run_env() {
  env -i PATH="$PATH" "$@" sh -c '. "$0"; for p in '"$SWITCHES"'; do n=${p%%=*}; printf "%s=%s\n" "$n" "$(eval "printf %s \"\${$n:-<unset>}\"")"; done' "$SCRIPT" 2>"$ERR"
}

echo "switch mapping"
out=$(run_env PIPESHUB_OFFLINE=1)
for pair in $SWITCHES; do
  check "sets $pair" '[[ "$out" == *"$pair"* ]]'
done
check "logs once what was set" '[[ "$(wc -l <"$ERR")" -eq 1 && "$(cat "$ERR")" == *"offline mode"*TELEMETRY_COLLECTION_FLAG* ]]'

for on in true TRUE yes on; do
  out=$(run_env PIPESHUB_OFFLINE=$on)
  check "PIPESHUB_OFFLINE=$on turns it on" '[[ "$out" == *"HF_HUB_OFFLINE=1"* ]]'
done

echo "off states change nothing"
for off in "" 0 false no; do
  out=$(run_env PIPESHUB_OFFLINE="$off")
  check "PIPESHUB_OFFLINE='$off' leaves HF_HUB_OFFLINE unset" '[[ "$out" == *"HF_HUB_OFFLINE=<unset>"* ]]'
  check "PIPESHUB_OFFLINE='$off' logs nothing" '[[ ! -s "$ERR" ]]'
done
out=$(run_env)
check "unset leaves telemetry flag unset" '[[ "$out" == *"TELEMETRY_COLLECTION_FLAG=<unset>"* ]]'

echo "operator values win"
out=$(run_env PIPESHUB_OFFLINE=1 TELEMETRY_COLLECTION_FLAG=true HF_HUB_OFFLINE=0)
check "explicit TELEMETRY_COLLECTION_FLAG kept" '[[ "$out" == *"TELEMETRY_COLLECTION_FLAG=true"* ]]'
check "explicit HF_HUB_OFFLINE=0 kept" '[[ "$out" == *"HF_HUB_OFFLINE=0"* ]]'
check "the other switches still set" '[[ "$out" == *"DO_NOT_TRACK=1"* ]]'
check "log names the kept values" '[[ "$(cat "$ERR")" == *"kept: TELEMETRY_COLLECTION_FLAG HF_HUB_OFFLINE"* ]]'

echo "applying twice is quiet"
env -i PATH="$PATH" PIPESHUB_OFFLINE=1 sh -c '. "$0"; . "$0"' "$SCRIPT" 2>"$ERR"
check "one log line for two applications" '[[ "$(wc -l <"$ERR")" -eq 1 ]]'

echo "entrypoint"
# As uid 1000 would, the non-root path execs the command directly; here the test
# user is not root either, so it exercises exactly that path.
echo "entrypoint copied alone (as entrypoint_ownership_test.sh does)"
alone=$(mktemp -d)
cp "$ENTRYPOINT" "$alone/docker-entrypoint.sh"
if [ "$(id -u)" != "0" ]; then
  out=$(env -i PATH="$PATH" sh "$alone/docker-entrypoint.sh" sh -c 'echo STARTED' 2>&1); rc=$?
  check "starts without offline-env.sh next to it" '[[ $rc -eq 0 && "$out" == "STARTED" ]]'
fi
rm -rf "$alone"
if [ "$(id -u)" != "0" ]; then
  out=$(env -i PATH="$PATH" PIPESHUB_OFFLINE=1 sh "$ENTRYPOINT" sh -c 'echo "$HF_HUB_OFFLINE $TELEMETRY_COLLECTION_FLAG"' 2>/dev/null)
  check "non-root start gets the switches" '[[ "$out" == "1 false" ]]'
  out=$(env -i PATH="$PATH" sh "$ENTRYPOINT" sh -c 'echo "[${HF_HUB_OFFLINE:-}]"' 2>/dev/null)
  check "no flag: nothing set" '[[ "$out" == "[]" ]]'
else
  pass "skipped non-root exec check (running as root)"
fi
first_exec=$(grep -n '^    \. .*offline-env.sh' "$ENTRYPOINT" | head -1 | cut -d: -f1)
uid_check=$(grep -n 'id -u' "$ENTRYPOINT" | head -1 | cut -d: -f1)
check "entrypoint applies the switches before the uid branch" '[[ -n "$first_exec" && -n "$uid_check" && "$first_exec" -lt "$uid_check" ]]'
# The sourcing line itself, not the comment above it.
check "process_monitor.sh sources it" 'grep -Eq "^\[ -f /usr/local/bin/offline-env.sh \] && \. /usr/local/bin/offline-env.sh$" "$ROOT/deployment/process_monitor.sh"'
check "entrypoint sources it only when it exists" 'grep -qxF "if [ -f \"\$(dirname \"\$0\")/offline-env.sh\" ]; then" "$ENTRYPOINT"'
check "image installs it next to the entrypoint" 'grep -q "deployment/offline-env.sh /usr/local/bin/" "$ROOT/Dockerfile"'
check "scripts parse" 'sh -n "$SCRIPT" && bash -n "$ENTRYPOINT"'

echo "dev frontend telemetry"
DEV="$ROOT/deployment/docker-compose/scripts/dev_process_monitor.sh"
block=$(sed -n '/^# offline-switch:/,/^# end offline-switch/p' "$DEV")
check "dev_process_monitor.sh has the offline block" '[[ -n "$block" ]]'
first_npm=$(grep -n '^npm run dev' "$DEV" | head -1 | cut -d: -f1)
block_line=$(grep -n '^# offline-switch:' "$DEV" | head -1 | cut -d: -f1)
check "the block runs before the first npm run dev" '[[ -n "$block_line" && "$block_line" -lt "$first_npm" ]]'
next_telemetry() { env -i PATH="$PATH" "$@" bash -c "$block"$'\n''printf %s "${NEXT_TELEMETRY_DISABLED:-<unset>}"'; }
check "offline disables Next.js telemetry" '[[ "$(next_telemetry PIPESHUB_OFFLINE=1)" == "1" ]]'
check "offline keeps an explicit value" '[[ "$(next_telemetry PIPESHUB_OFFLINE=true NEXT_TELEMETRY_DISABLED=0)" == "0" ]]'
check "empty leaves telemetry as it was" '[[ "$(next_telemetry PIPESHUB_OFFLINE=)" == "<unset>" ]]'
check "unset leaves telemetry as it was" '[[ "$(next_telemetry)" == "<unset>" ]]'
check "off values leave telemetry as it was" '[[ "$(next_telemetry PIPESHUB_OFFLINE=0)" == "<unset>" ]]'
check "dev script parses" 'bash -n "$DEV"'

if [ "$FAILS" -ne 0 ]; then
  echo "$FAILS failed"
  exit 1
fi
echo "all passed"
