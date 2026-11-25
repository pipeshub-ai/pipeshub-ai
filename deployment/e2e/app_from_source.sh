#!/usr/bin/env bash
# The app container of the E2E stack, from source: what the image's process
# monitor (Dockerfile, process_monitor.sh) starts, in the same
# order, without building the image. Node runs the tsc build run.sh made of
# this tree (laid out as the image lays it out: dist/ with node_modules inside
# it, src/ beside it); the Python roles run from this tree's backend/python
# with a dev venv's site-packages (pyrun.py). Embedding and docling are not
# started; indexing only on the matrix lane (E2E_INDEXING=1). The durable worker
# (app.harness_main) starts only on the durable lane (E2E_DURABLE=1, run.sh
# durable), restarted when it dies as the image's process monitor restarts it.
#
# Each process logs to /e2e-logs/<name>.log so run.sh can name the one that did
# not come up, and opens it for append so rotate_logs.sh can bound it by truncating in place.
# E2E_FAULT=<name> skips exactly that process (run.sh ST-2).
set -uo pipefail

# E2E_LOGS_DIR: a test runs this script outside the container (tests/cohesion/test_r46_lane_scripts.py).
LOGS=${E2E_LOGS_DIR:-/e2e-logs}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export PATH=/opt/node/bin:$PATH
export HOME=/tmp
cd /tmp || exit 1
bash "$HERE/rotate_logs.sh" "$LOGS" &

# <name>.exited holds the exit status of a process that stopped, so run.sh fails fast and names it; <name>.pid the
# process itself (never a wrapper shell), which the stop below signals. A command, not a shell function: a function
# would run in a subshell, and its pid would be the shell's.
start() {
  local name=$1
  shift
  rm -f "$LOGS/$name.exited"
  if [[ "${E2E_FAULT:-}" == "$name" ]]; then
    echo "E2E_FAULT: not starting $name" >"$LOGS/$name.log"
    echo 1 >"$LOGS/$name.exited"
    return
  fi
  : >"$LOGS/$name.log"
  {
    "$@" &
    echo "$!" >"$LOGS/$name.pid"
    wait "$!"
    echo "$?" >"$LOGS/$name.exited"
  } >>"$LOGS/$name.log" 2>&1 &
  echo "started $name (pid $!)"
}

# A supervised process (the durable worker; the query service and Node on the QA-6 lane) restarts when it dies, as the
# image's process monitor restarts it. Two control files steer it:
#   /e2e-control/kill-<name>     SIGKILL it (no drain, as a crash; E2E2 DE-5), the pid goes to kill-<name>.done;
#   /e2e-control/restart-<name>  stop it (SIGTERM) and start it again, the start count goes to restart-<name>.done.
# Each start reads /e2e-control/<name>.env (KEY=VALUE lines) over the container's environment, so the QA-6 runner
# sets the process settings a matrix row needs (HARNESS_FLOW_GUARD, SANDBOX_MODE, ...) as an operator would.
supervise() {
  local name=$1 n=0 pid
  shift
  while true; do
    n=$((n + 1))
    (
      if [[ -f "/e2e-control/$name.env" ]]; then
        set -a
        # shellcheck disable=SC1090
        . "/e2e-control/$name.env"
        set +a
      fi
      exec "$@"
    ) >>"$LOGS/$name.log" 2>&1 &
    pid=$!
    echo "$pid" >"$LOGS/$name.pid"
    echo "$name process $n started (pid $pid)" >>"$LOGS/$name-supervisor.log"
    while kill -0 "$pid" 2>/dev/null; do
      if [[ -f "/e2e-control/kill-$name" ]]; then
        kill -9 "$pid" 2>/dev/null
        wait "$pid" 2>/dev/null
        echo "$pid" >"/e2e-control/kill-$name.done"
        rm -f "/e2e-control/kill-$name"
        echo "$name process $n killed on request (pid $pid)" >>"$LOGS/$name-supervisor.log"
        break
      fi
      if [[ -f "/e2e-control/restart-$name" ]]; then
        kill -TERM "$pid" 2>/dev/null
        wait "$pid" 2>/dev/null
        rm -f "/e2e-control/restart-$name"
        echo "$((n + 1))" >"/e2e-control/restart-$name.done"
        echo "$name process $n stopped for a restart (pid $pid)" >>"$LOGS/$name-supervisor.log"
        break
      fi
      sleep 0.5
    done
    wait "$pid" 2>/dev/null
    echo "$name process $n exited (pid $pid, status $?)" >>"$LOGS/$name-supervisor.log"
    sleep 1
  done
}

start_supervised() {
  local name=$1
  shift
  if [[ "${E2E_FAULT:-}" == "$name" ]]; then
    echo "E2E_FAULT: not starting $name" >"$LOGS/$name.log"
    echo 1 >"$LOGS/$name.exited"
    return
  fi
  supervise "$name" "$@" &
  SUPERVISORS+=("$!")
  echo "started the $name supervisor (pid $!)"
}
SUPERVISORS=()

# A person's stack (run.sh human-up) calls the model providers a person configures, not the scripted model, and
# embeds with the product's default local model, as the image does.
if [[ "${E2E_HUMAN:-}" == "1" ]]; then
  unset ANTHROPIC_BASE_URL GOOGLE_GEMINI_BASE_URL OPENAI_BASE_URL AWS_ENDPOINT_URL_BEDROCK_RUNTIME
  start_supervised embedding python /e2e/pyrun.py app.embedding_main
fi
# R35-SLACK (E2E_FAKE_SLACK=1, human-up's default): the scripted Slack workspace (fake_saas/fake_slack.py) is Slack's Web
# API for the bot and for Node's own Slack messages, and the bot runs as the image's process monitor starts it.
if [[ "${E2E_FAKE_SLACK:-}" == "1" ]]; then
  export SLACK_API_URL=http://pxh-fake-saas:8090/slack/api/
fi
# A person's stack lives for days, so Node restarts when it exits, as the image's process monitor restarts it
# (OPS2: a Node that stopped on a full disk left the port dead for hours). Test lanes keep failing fast instead,
# but the QA-6 lane's: Node pins the deployment's flow rule into every run (CONTROL_PLANE_PIN), so a row's process
# settings must reach it as they reach the worker (QAFINAL-FU-PW012).
if [[ "${E2E_HUMAN:-}" == "1" || "${E2E_SUPERVISE_QUERY:-}" == "1" ]]; then
  start_supervised node bash -c 'cd /app/backend && exec node --experimental-async-context-frame dist/index.js'
else
  start node bash -c 'cd /app/backend && exec node --experimental-async-context-frame dist/index.js'
fi
if [[ "${E2E_FAKE_SLACK:-}" == "1" ]]; then
  start slack-bot bash -c 'cd /app/backend && exec node dist/integrations/slack-bot/src/index.js'
fi
start connectors python /e2e/pyrun.py app.connectors_main
if [[ "${E2E_SUPERVISE_QUERY:-}" == "1" ]]; then
  start_supervised query python /e2e/pyrun.py app.query_main
else
  start query python /e2e/pyrun.py app.query_main
fi
# The image's process monitor starts the gateway with the harness (ADR-OPS-06); a lane with it off (run.sh off) runs none.
if [[ "${AGENT_HARNESS_WORKER_ENABLED:-false}" == "true" ]]; then
  start model-gateway python /e2e/pyrun.py app.model_gateway_main
fi
# QA-6 (matrix lane): knowledge rows read a record the stack indexed itself, parsed in-process and embedded
# by the scripted model's /v1/embeddings.
# Supervised: indexing exits when the connector service is not up yet, and the image's process monitor starts it again.
if [[ "${E2E_INDEXING:-}" == "1" ]]; then
  start_supervised indexing python /e2e/pyrun.py app.indexing_main
fi
if [[ "${E2E_DURABLE:-}" == "1" && "${AGENT_HARNESS_WORKER_ENABLED:-false}" == "true" ]]; then
  start_supervised worker python /e2e/pyrun.py app.harness_main
fi

# A stop (`docker compose stop`, its TERM forwarded by the init) reaches the processes themselves, as the image's
# process monitor stops them (process_monitor.sh cleanup): the supervisors first, so nothing restarts; then the durable
# worker and the query service, which drain their runs (finish the boundary, commit through Node, yield) within
# DRAIN_WAIT_S while Node still answers; then the rest, Node with them. The app's stop_grace_period (60 s) covers it. Signalling only this script's jobs (R44STOP-FU-1) reached the wrapper shells:
# the worker was killed with the container, and never drained.
DRAIN_FIRST=(worker query)
DRAIN_WAIT_S=${E2E_DRAIN_WAIT_S:-40}

# TERM to each named process with a pid file that still runs, then wait for them all, up to DRAIN_WAIT_S.
stop_and_wait() {
  local name pid i live pids=()
  for name in "$@"; do
    [[ -f "$LOGS/$name.pid" ]] || continue
    pid=$(<"$LOGS/$name.pid")
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null && pids+=("$pid") && echo "stop: TERM to $name (pid $pid)"
    fi
  done
  for ((i = 0; i < DRAIN_WAIT_S * 2; i++)); do
    live=0
    for pid in "${pids[@]}"; do
      kill -0 "$pid" 2>/dev/null && live=1
    done
    [[ $live -eq 0 ]] && return 0
    sleep 0.5
  done
  echo "stop: the wait of ${DRAIN_WAIT_S}s is over with some of $* still running"
}

stop_all() {
  trap '' TERM INT
  local f name rest=()
  ((${#SUPERVISORS[@]})) && kill "${SUPERVISORS[@]}" 2>/dev/null
  stop_and_wait "${DRAIN_FIRST[@]}"
  for f in "$LOGS"/*.pid; do
    [[ -f "$f" ]] || continue
    name=$(basename "$f" .pid)
    [[ " ${DRAIN_FIRST[*]} " == *" $name "* ]] || rest+=("$name")
  done
  stop_and_wait "${rest[@]}"
  kill $(jobs -p) 2>/dev/null
  echo "stop: every process stopped"
  exit 0
}
trap stop_all TERM INT
wait
