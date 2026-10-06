#!/usr/bin/env bash
# One command for the collaborative-chats browser journeys.
#
#   frontend/tests/e2e/collab-chats/run.sh                       # every spec in this directory
#   frontend/tests/e2e/collab-chats/run.sh j04                   # extra args go to `playwright test`
#
# Boots, in order: the lane's backend (Mongo + Redis containers `pcc-e2e-*`, the real Node API from source, a scriptable
# fake of the Python services, flag on) -> the Next.js dev server pointed at that Node API -> Playwright inside the
# official Playwright image as container `pcc-pw-*`. Everything is removed on exit, also on failure and Ctrl-C. Never
# touches a container it did not create.
#
# The browser runs in the container's own network namespace and reaches the host's loopback ports (Next, Node API,
# control API) through `support/netns-bridge.cjs` over Unix sockets in the run directory. With `--network host`
# Chromium failed in-flight requests with net::ERR_NETWORK_CHANGED whenever any container on this shared host started
# or stopped. `PCC_PW_HOST_NETWORK=1` restores host networking.
#
# Iterating on a spec without paying the boot every time:
#   PCC_E2E_HOLD=1 PCC_E2E_RUN_DIR=/tmp/x run.sh          # boots, prints the URLs, then waits (Ctrl-C or `touch /tmp/x/stop` ends it)
#   PCC_E2E_ATTACH=/tmp/x run.sh j03                      # in another shell: only runs Playwright against that stack
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FE_DIR="$(cd "$HERE/../../.." && pwd)"
REPO="$(cd "$FE_DIR/.." && pwd)"
IT_DIR="$REPO/integration-tests"
NODE_ROOT="$REPO/backend/nodejs/apps"

export PATH="${PCC_E2E_NODE_BIN:-$HOME/.local/node/bin}:$PATH"
export TMPDIR="${TMPDIR:-/tmp/pcc-e2e/tmp}"   # keep it short: the lane uses Unix sockets
mkdir -p "$TMPDIR"

# Must match @playwright/test in frontend/package.json (the browser build is bound to the library version).
PW_VERSION="$(node -p "require('$FE_DIR/node_modules/@playwright/test/package.json').version" 2>/dev/null || echo 1.59.1)"
PW_IMAGE="${PCC_PW_IMAGE:-mcr.microsoft.com/playwright:v${PW_VERSION}-jammy}"

PYTHON="${PCC_E2E_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  for candidate in "${PCC_E2E_VENV:-}/bin/python" /tmp/pcc-e2e/itvenv/bin/python "$IT_DIR/.venv/bin/python"; do
    [[ -x "$candidate" ]] && PYTHON="$candidate" && break
  done
fi
[[ -n "$PYTHON" ]] || { echo "no python: build the integration venv ('uv sync --frozen' in integration-tests) and set PCC_E2E_VENV" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }

[[ -x "$NODE_ROOT/node_modules/.bin/ts-node-transpile-only" ]] || (cd "$NODE_ROOT" && npm ci --no-audit --no-fund) || exit 2
[[ -d "$FE_DIR/node_modules/@playwright/test" ]] || (cd "$FE_DIR" && npm ci --no-audit --no-fund) || exit 2

ID="$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
ATTACH="${PCC_E2E_ATTACH:-}"
if [[ -n "$ATTACH" ]]; then
  RUN_DIR="$ATTACH"
else
  OWN_RUN_DIR=0
  if [[ -n "${PCC_E2E_RUN_DIR:-}" ]]; then
    RUN_DIR="$PCC_E2E_RUN_DIR"
    mkdir -p "$RUN_DIR"
    # A reused directory must not hand a dead stack's state (or a stale stop request) to this run.
    rm -f "$RUN_DIR/state.json" "$RUN_DIR/stop" "$RUN_DIR/frontend-origin"
  else
    RUN_DIR="$(mktemp -d "$TMPDIR/pcc-pw-run-XXXXXX")"
    OWN_RUN_DIR=1
  fi
fi
STATE="$RUN_DIR/state.json"
PW_NAME="pcc-pw-$ID"
export PCC_E2E_PROJECT="${PCC_E2E_PROJECT:-pcc-e2e-$ID}"

free_port() { "$PYTHON" -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])"; }
FE_PORT="${PCC_E2E_FE_PORT:-$(free_port)}"
FE_ORIGIN="http://localhost:$FE_PORT"

SERVE_PID="" NEXT_PID="" BRIDGE_PID=""
cleanup() {
  trap - EXIT INT TERM
  docker rm -f "$PW_NAME" >/dev/null 2>&1 || true
  if [[ -n "$BRIDGE_PID" ]]; then kill -TERM "$BRIDGE_PID" 2>/dev/null || true; fi
  if [[ -n "$NEXT_PID" ]]; then kill -TERM -- "-$NEXT_PID" 2>/dev/null || true; fi
  if [[ -n "$SERVE_PID" ]] && kill -0 "$SERVE_PID" 2>/dev/null; then
    # SIGTERM runs the serve process's own teardown (Node API, fake, `docker compose down -v`).
    kill -TERM "$SERVE_PID" 2>/dev/null || true
    for _ in $(seq 1 90); do kill -0 "$SERVE_PID" 2>/dev/null || break; sleep 1; done
    kill -KILL "$SERVE_PID" 2>/dev/null || true
  fi
  docker compose -p "$PCC_E2E_PROJECT" -f "$IT_DIR/collaborative-chats/stack/docker-compose.yml" down -v --remove-orphans >/dev/null 2>&1 || true
  if [[ -n "$NEXT_PID" ]]; then kill -KILL -- "-$NEXT_PID" 2>/dev/null || true; fi
  # Only a directory this script made is removed; a caller's PCC_E2E_RUN_DIR is left in place.
  [[ "${PCC_E2E_KEEP_RUN_DIR:-0}" == 1 || "${OWN_RUN_DIR:-0}" != 1 ]] || rm -rf "$RUN_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

boot_stack() {
echo "== backend (Node API, Mongo, Redis, fake of the Python services)"
(cd "$IT_DIR" && exec "$PYTHON" -m helper.collab_stack.serve --state-file "$STATE" --frontend-origin "$FE_ORIGIN") >"$RUN_DIR/serve.log" 2>&1 &
SERVE_PID=$!
for _ in $(seq 1 300); do
  [[ -f "$STATE" ]] && break
  kill -0 "$SERVE_PID" 2>/dev/null || { echo "backend exited early:" >&2; tail -40 "$RUN_DIR/serve.log" >&2; exit 3; }
  sleep 1
done
[[ -f "$STATE" ]] || { echo "backend did not become ready in 300 s" >&2; tail -40 "$RUN_DIR/serve.log" >&2; exit 3; }
NODE_PORT="$(node -p "new URL(require('$STATE').nodeUrl).port")"
echo "node api:   http://localhost:$NODE_PORT"
node -e "const s=require('$STATE');console.log(JSON.stringify({nodeUrl:s.nodeUrl,controlUrl:s.controlUrl,roster:Object.fromEntries(Object.entries(s.roster).map(([k,v])=>[k,{userId:v.userId,email:v.email}]))}))"

echo "== frontend (next dev on $FE_ORIGIN -> http://localhost:$NODE_PORT)"
(cd "$FE_DIR" && NEXT_PUBLIC_API_BASE_URL="http://localhost:$NODE_PORT" NEXT_TELEMETRY_DISABLED=1 exec setsid npx next dev --turbopack -p "$FE_PORT") >"$RUN_DIR/next.log" 2>&1 &
NEXT_PID=$!
for _ in $(seq 1 120); do
  curl -sf -o /dev/null "$FE_ORIGIN/login/" && break
  kill -0 "$NEXT_PID" 2>/dev/null || { echo "next dev exited early:" >&2; tail -40 "$RUN_DIR/next.log" >&2; exit 3; }
  sleep 2
done
# Compile the routes the journeys open now, so no test pays for a cold Turbopack compile.
for route in /login/ /chat/; do curl -s -o /dev/null --max-time 180 "$FE_ORIGIN$route" || true; done
CONTROL_PORT="$(node -p "new URL(require('$STATE').controlUrl).port")"
mkdir -p "$RUN_DIR/net"
node "$HERE/support/netns-bridge.cjs" host "$RUN_DIR/net" "$FE_PORT" "$NODE_PORT" "$CONTROL_PORT" &
BRIDGE_PID=$!
echo "$FE_PORT $NODE_PORT $CONTROL_PORT" >"$RUN_DIR/bridge-ports"
echo "$FE_ORIGIN" >"$RUN_DIR/frontend-origin"
}

if [[ -z "$ATTACH" ]]; then
  boot_stack
else
  trap - EXIT   # the stack belongs to the shell that booted it
  FE_ORIGIN="$(cat "$RUN_DIR/frontend-origin")"
fi

if [[ -z "$ATTACH" && "${PCC_E2E_HOLD:-0}" == 1 ]]; then
  echo "== holding: run 'PCC_E2E_ATTACH=$RUN_DIR $0 <args>' to run specs; touch $RUN_DIR/stop (or Ctrl-C) to tear down"
  while [[ ! -e "$RUN_DIR/stop" ]]; do sleep 1; done
  exit 0
fi

echo "== playwright ($PW_IMAGE as $PW_NAME)"
mkdir -p "$FE_DIR/test-results" "$FE_DIR/playwright-report"
[[ -n "$ATTACH" ]] && trap 'docker rm -f "$PW_NAME" >/dev/null 2>&1' EXIT
PW_CMD=(npx playwright test --project collab-chats --reporter=list,html "$@")
PW_NET=(--network host)
PW_MOUNTS=(-v "$RUN_DIR:$RUN_DIR:ro")
if [[ "${PCC_PW_HOST_NETWORK:-0}" != 1 && -f "$RUN_DIR/bridge-ports" ]]; then
  read -r -a BRIDGE_PORTS <"$RUN_DIR/bridge-ports"
  PW_NET=()
  PW_MOUNTS+=(-v "$RUN_DIR/net:$RUN_DIR/net")
  PW_CMD=(node tests/e2e/collab-chats/support/netns-bridge.cjs guest "$RUN_DIR/net" "${BRIDGE_PORTS[@]}" -- "${PW_CMD[@]}")
fi
docker run --rm --name "$PW_NAME" "${PW_NET[@]}" --ipc=host --init \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e BASE_URL="$FE_ORIGIN" -e PCC_STACK_STATE="$STATE" -e PCC_VISUAL_TOUR="${PCC_VISUAL_TOUR:-}" -e PLAYWRIGHT_NO_SERVER=1 -e CI="${CI:-}" \
  -v "$FE_DIR:/work" "${PW_MOUNTS[@]}" -w /work \
  "$PW_IMAGE" "${PW_CMD[@]}"
status=$?

if [[ $status -ne 0 ]]; then
  echo "--- node api log tail" >&2; tail -30 "$(ls -t "$RUN_DIR"/pcc-e2e-run-*/node-*.log "$TMPDIR"/pcc-e2e-run-*/node-*.log 2>/dev/null | head -1)" 2>/dev/null >&2 || true
fi
exit $status
