#!/usr/bin/env bash
# Runs the cost-class benchmark over app tiers, one compose stack per tier, and writes a summary.
#
#   BENCH_TIERS="T4:4:16 T16:16:64" integration-tests/perf/run_tiers_e2e.sh --scenario b05 [--dry-run] [-- <bench_costclass args>]
#
# Per tier: pick idle cores (10 s of /proc/stat), check the tier fits (or print SKIP with the reason),
# refuse to start a tier while the host is at 85 % CPU or memory (checked before every tier; retry, then skip it
# and exit 2), bring the stack up with
# deployment/docker-compose/docker-compose.bench.yml, run bench_costclass.py, tear the stack down with -v.
# --dry-run prints the plan and starts nothing.
#
# Env: BENCH_TIERS (default "T4:4:16"), BENCH_PROJECT (compose project, default bench-run),
#      BENCH_PORT (first host port, default 19500), BENCH_LABEL, BENCH_QUIET_RETRIES, BENCH_QUIET_SLEEP,
#      BENCH_ORDER (comma-separated CPU numbers, idlest first; for tests), BENCH_OUT (result dir),
#      BENCH_REPEATS (default 1; the number of runs of every tier, each on a fresh stack. Above 1, the runner then
#      checks that the repeats are a valid baseline: all pass and p50 CV at most 10 percent, else it exits 3).
# Large host: BENCH_TIERS="T16:16:64 T32:32:128 T48:48:192 T96:96:256" with the siblings scaled as in
# the capacity design.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
compose_dir="$repo/deployment/docker-compose"
scenario="" dry=0 extra=()
while [ $# -gt 0 ]; do
  case "$1" in
    --scenario) scenario=$2; shift 2 ;;
    --dry-run) dry=1; shift ;;
    --) shift; extra=("$@"); break ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
[ -n "$scenario" ] || { echo "--scenario is required" >&2; exit 64; }

tiers=${BENCH_TIERS:-T4:4:16}
project=${BENCH_PROJECT:-bench-run}  # lower case: compose rejects capitals
port=${BENCH_PORT:-19500}
label=${BENCH_LABEL:-local}
repeats=${BENCH_REPEATS:-1}
[[ "$repeats" =~ ^[1-9][0-9]*$ ]] || { echo "BENCH_REPEATS must be a positive integer" >&2; exit 64; }
out=${BENCH_OUT:-$repo/integration-tests/reports/perf}
# The plan for a tier list. Run again for one tier after its quiet check, so the cores it gets are the idlest
# now and not those of the first plan (another session may have taken them since).
plan_for() {
  local args=(plan --tiers "$1" --scenario "$scenario")
  [ -n "${BENCH_ORDER:-}" ] && args+=(--order "$BENCH_ORDER")
  [ -n "${BENCH_HOST_CPUS:-}" ] && args+=(--host-cpus "$BENCH_HOST_CPUS")
  [ -n "${BENCH_HOST_MEM_GB:-}" ] && args+=(--host-mem-gb "$BENCH_HOST_MEM_GB")
  python3 "$here/tiers.py" "${args[@]}"
}

plan=$(plan_for "$tiers")  # a malformed or duplicated tier list stops here, before any stack starts
echo "$plan"
[ "$dry" = 1 ] && exit 0

# A fresh stack has no org: create one with a throwaway admin (the password is never printed or stored)
# and skip onboarding, as the benchmark workflows do.
bootstrap_org() {
  local base=$1 email="bench-$$@example.com" password hdrs session token
  password="Bench-$(openssl rand -hex 12)-1aA"
  jq -n --arg e "$email" --arg p "$password" '{accountType:"business",adminFullName:"Bench Admin",contactEmail:$e,password:$p,confirmPassword:$p,registeredName:"Bench Org",shortName:"BENCH"}' \
    | curl -sf "$base/api/v1/org" -H "Content-Type: application/json" --data-binary @- >/dev/null || return 1
  hdrs=$(mktemp)
  jq -n --arg e "$email" '{email:$e}' | curl -sf "$base/api/v1/userAccount/initAuth" -H "Content-Type: application/json" --data-binary @- -D "$hdrs" -o /dev/null || return 1
  session=$(grep -i "x-session-token:" "$hdrs" | tr -d '\r' | awk '{print $2}'); rm -f "$hdrs"
  token=$(jq -n --arg e "$email" --arg p "$password" '{method:"password",credentials:{password:$p},email:$e}' \
    | curl -sf "$base/api/v1/userAccount/authenticate" -H "Content-Type: application/json" -H "x-session-token: $session" --data-binary @- | jq -r '.accessToken')
  curl -sf -X PUT "$base/api/v1/org/onboarding-status" -H "Content-Type: application/json" -H "Authorization: Bearer $token" -d '{"status":"configured"}' >/dev/null || return 1
  export PIPESHUB_TEST_ENV=local PIPESHUB_TEST_USER_EMAIL=$email PIPESHUB_TEST_USER_PASSWORD=$password
}

mkdir -p "$out"

# Flags the stack itself has to take, read from the bench arguments: the fake provider's latency and
# embedding mode go to its container.
fake_args=""
for ((i = 0; i < ${#extra[@]}; i++)); do
  case "${extra[$i]}" in
    --fake-ai-base-ms) fake_args="$fake_args --base-ms ${extra[$((i + 1))]}" ;;
    --fake-embeddings) fake_args="$fake_args --fake-embeddings" ;;
  esac
done
export BENCH_FAKE_AI_ARGS="${fake_args# }"

ran=0 skipped=0 rc=0 results=()
: >"$out/summary.md"
for ((rep = 1; rep <= repeats; rep++)); do
rep_suffix=""; [ "$repeats" -gt 1 ] && rep_suffix="-r$rep"
while IFS= read -r line <&3; do
  case "$line" in
    SKIP*) echo "$line" >&2; skipped=$((skipped + 1)); continue ;;
    RUN*) ;;
    *) continue ;;
  esac
  name=$(awk '{print $2}' <<<"$line")
  get() { sed -n "s/.* $1=\([^ ]*\).*/\1/p" <<<"$line"; }
  cpus=$(get cpus); mem=$(get mem)
  # Every tier starts on a quiet host: the one before it, or another session, may have changed that.
  if ! python3 "$here/tiers.py" quiet --retries "${BENCH_QUIET_RETRIES:-5}" --sleep "${BENCH_QUIET_SLEEP:-60}"; then
    echo "SKIP $name: the host is not quiet" >&2
    skipped=$((skipped + 1)); rc=$(( rc > 2 ? rc : 2 )); continue
  fi
  fresh=$(plan_for "$name:$cpus:${mem%g}" | grep '^RUN ' || true)
  if [ -z "$fresh" ]; then
    echo "SKIP $name: it no longer fits after the host check" >&2
    skipped=$((skipped + 1)); rc=$(( rc > 2 ? rc : 2 )); continue
  fi
  line=$fresh
  export BENCH_PORT_HTTP=$port BENCH_APP_CPUS=$cpus BENCH_APP_MEM=$mem
  export BENCH_APP_CPUSET=$(get app) BENCH_FAKE_AI_CPUSET=$(get fake_ai) BENCH_AUX_CPUSET=$(get aux)
  export BENCH_NEO4J_CPUSET=$(get neo4j) BENCH_QDRANT_CPUSET=$(get qdrant)
  export BENCH_REDIS_CPUSET=$(get redis) BENCH_MONGO_CPUSET=$(get mongo)
  for s in NEO4J QDRANT REDIS MONGO; do
    set_var="BENCH_${s}_CPUSET"
    export "BENCH_${s}_CPUS=$(python3 - "${!set_var}" <<'PY'
import sys
total = 0
for part in sys.argv[1].split(","):
    lo, _, hi = part.partition("-")
    total += int(hi or lo) - int(lo) + 1
print(total)
PY
)"
  done
  proj="$project-${name,,}"
  compose=(docker compose -p "$proj" -f "$compose_dir/docker-compose.integration.neo4j.yml" -f "$compose_dir/docker-compose.bench.yml")
  # An extra compose file, for example to run a prebuilt image instead of building the checkout.
  [ -n "${BENCH_COMPOSE_EXTRA:-}" ] && compose+=(-f "$BENCH_COMPOSE_EXTRA")
  down() { "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; }
  trap down EXIT
  tier_rc=0
  "${compose[@]}" up -d pipeshub-ai fake-ai || tier_rc=1
  base="http://localhost:$port"
  deadline=$((SECONDS + 600))
  if [ "$tier_rc" = 0 ]; then
    until curl -sf "$base/api/v1/health/services" | grep -q '"status":"healthy"'; do
      [ "$SECONDS" -lt "$deadline" ] || { echo "stack for $name did not become healthy" >&2; tier_rc=1; break; }
      sleep 10
    done
  fi
  if [ "$tier_rc" = 0 ]; then
    bootstrap_org "$base" || tier_rc=$?
  fi
  if [ "$tier_rc" = 0 ]; then
    ran=$((ran + 1))
    result="$out/costclass-$label-$name$rep_suffix.json"
    rm -f "$result" "$out/summary-$name$rep_suffix.md"  # nothing of an earlier run may be mistaken for this one
    python3 "$here/bench_costclass.py" --scenario "$scenario" --label "$label-$name" \
      --base-url "$base" --tier-cpus "$cpus" --tier-mem-gb "${mem%g}" \
      --fake-ai-endpoint "http://fake-ai:8080/v1" --fake-ai-container "$proj-fake-ai-1" \
      --app-container "$proj-pipeshub-ai-1" \
      --siblings "$proj-neo4j-1,$proj-qdrant-1,$proj-redis-1,$proj-mongodb-1" \
      --redis-container "$proj-redis-1" --neo4j-container "$proj-neo4j-1" \
      --output "$result" --summary "$out/summary-$name$rep_suffix.md" "${extra[@]}" || tier_rc=$?
    [ -f "$out/summary-$name$rep_suffix.md" ] && cat "$out/summary-$name$rep_suffix.md" >>"$out/summary.md"
    [ -f "$result" ] && results+=("$result")
  else
    echo "FAIL $name: the stack did not come up or the organization could not be created" >&2
  fi
  down; trap - EXIT
  [ "$tier_rc" -gt "$rc" ] && rc=$tier_rc  # a failed tier does not stop the next ones: the knee needs every point
  port=$((port + 1))
done 3<<<"$plan"
done

echo "tiers: $ran ran, $skipped skipped" >&2
if [ "$ran" = 0 ]; then
  echo "nothing was measured: every tier was skipped or failed to start" >&2
  [ "$rc" = 0 ] && rc=4
fi

# The knee: CELL_CPUS and its confidence from the tiers' results, valid runs only.
if [ "$scenario" = b06-knee ] && [ "${#results[@]}" -gt 0 ]; then
  python3 "$here/bench_costclass.py" --knee-from "${results[@]}" --output "$out/knee-$label.json" || rc=$(( rc > 1 ? rc : 1 ))
fi
# Repeats: a baseline is valid only when every repeat passed and the p50 of every kind is steady (CV at most 10 percent).
if [ "$repeats" -gt 1 ] && [ "${#results[@]}" -gt 0 ]; then
  python3 "$here/bench_costclass.py" --baseline-from "${results[@]}" --expected-repeats "$repeats" --output "$out/baseline-$label.json" || rc=$(( rc > 3 ? rc : 3 ))
fi
exit "$rc"
