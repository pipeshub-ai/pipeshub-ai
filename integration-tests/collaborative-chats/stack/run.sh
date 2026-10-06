#!/usr/bin/env bash
# One command for the collaborative-chats real-HTTP lane: boots Mongo + Redis containers and the
# Node API, runs the journeys, tears everything down (also on failure or Ctrl-C).
#
#   collaborative-chats/stack/run.sh                      # every collab_stack journey
#   collaborative-chats/stack/run.sh -k j01               # extra args go to pytest
#   PCC_E2E_PYTHON=/path/to/python collaborative-chats/stack/run.sh
#   collaborative-chats/stack/run.sh --real-python [--graph arangodb] [pytest args]   # real query + connectors services
#
# Never uses the default compose project: all containers are named pcc-e2e-<id>-*.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IT_DIR="$(cd "$HERE/../.." && pwd)"
export PATH="${PCC_E2E_NODE_BIN:-$HOME/.local/node/bin}:$PATH"
export TMPDIR="${TMPDIR:-/tmp/pcc-e2e/tmp}"
mkdir -p "$TMPDIR"

PYTHON="${PCC_E2E_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  for candidate in "${PCC_E2E_VENV:-}/bin/python" /tmp/pcc-e2e/itvenv/bin/python "$IT_DIR/.venv/bin/python"; do
    [[ -x "$candidate" ]] && PYTHON="$candidate" && break
  done
fi
if [[ -z "$PYTHON" ]]; then
  echo "no python found: build the venv with 'uv sync --frozen' in integration-tests and set PCC_E2E_VENV" >&2
  exit 2
fi

# --real-python [--graph neo4j|arangodb]: start the Python query and connectors services from source (PCC_E2E_REAL_PYTHON=1).
while [[ $# -gt 0 ]]; do
  case "$1" in
    --real-python) export PCC_E2E_REAL_PYTHON=1; shift ;;
    --graph) export PCC_E2E_GRAPH="$2"; shift 2 ;;
    *) break ;;
  esac
done

NODE_ROOT="${PCC_E2E_NODE_ROOT:-$(cd "$IT_DIR/../backend/nodejs/apps" && pwd)}"
if [[ ! -x "$NODE_ROOT/node_modules/.bin/ts-node-transpile-only" ]]; then
  echo "installing Node dependencies in $NODE_ROOT" >&2
  (cd "$NODE_ROOT" && npm ci --no-audit --no-fund) || exit 2
fi

export PCC_E2E_PROJECT="${PCC_E2E_PROJECT:-pcc-e2e-$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n')}"

cleanup() {
  docker compose -p "$PCC_E2E_PROJECT" -f "$HERE/docker-compose.yml" --profile real-python --profile neo4j --profile arango down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM

cd "$IT_DIR"
"$PYTHON" -m pytest collaborative-chats -m "collab_stack" -p no:cacheprovider "$@"
status=$?

cleanup
left="$(docker ps -a --filter "label=com.docker.compose.project=$PCC_E2E_PROJECT" --format '{{.Names}}')"
if [[ -n "$left" ]]; then
  echo "containers left behind: $left" >&2
  exit 3
fi
exit $status
