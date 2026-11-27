#!/usr/bin/env bash
# Runs connectors-simulator's GitHub harness against this checkout.
#   github_sim_harness.sh "<backends>" [scenario or area ...]    (no scenario: every one)
# Expects the simulator checked out at $GITHUB_WORKSPACE/connectors-simulator with `uv sync` done, the backend's
# packages on the active Python, and the graph stores named by NEO4J_IT_URI / ARANGO_IT_URL.
set -euo pipefail

backends=$1
shift
root=${GITHUB_WORKSPACE:-$(pwd)}
sim_dir=$root/connectors-simulator
out=${RUNNER_TEMP:-/tmp}/github-sim
port=${GH_SIM_PORT:-8126}

cd "$sim_dir"
uv run connsim serve github --port "$port" > "$out.server.log" 2>&1 &
server=$!
trap 'kill "$server" 2>/dev/null || true' EXIT
for _ in $(seq 60); do
  curl -fsS "http://127.0.0.1:$port/_sim/health" > /dev/null 2>&1 && break
  kill -0 "$server" || { cat "$out.server.log"; exit 1; }
  sleep 1
done
curl -fsS "http://127.0.0.1:$port/_sim/health" > /dev/null

only=()
if [ "$#" -gt 0 ]; then
  only=(--only "$@")
fi
# shellcheck disable=SC2086 # the backends are one word each
PIPESHUB_BACKEND="$root/backend/python" python harness/github/run.py --sim "http://127.0.0.1:$port" \
  --backend $backends --out "$out" "${only[@]}"
