#!/usr/bin/env bash
# Code-sandbox variables reach the services that read them, in every Compose
# file that runs the coding sandbox (needs `docker compose config` only; starts nothing).
#  1. With an empty environment each variable has its default.
#  2. A value in the env file reaches every service that reads it.
# The app service is the one that sets SANDBOX_MODE; the proxy is
# docker-socket-proxy, in the files that have one.
# Set DOCKER="sudo -n docker" if the daemon socket needs it.
set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FILES=(
  docker-compose.yml
  docker-compose.build.neo4j.yml
  docker-compose.prod-legacy.yml
  beta/docker-compose.build.neo4j.slim.yml
  beta/docker-compose.build.opensearch.neo4j.yml
  docker-compose.integration.arango.yml
  docker-compose.integration.neo4j.yml
)

# variable|default|override|where ("app", "proxy" or "app proxy")
VARS=(
  "SANDBOX_MAX_RUN_TIMEOUT_S|300|900|app proxy"
  "DOCKER_PROXY_INSTANCE||stack-a|proxy"
)

# render <compose-file> <out.json> [VAR=value...]: every profile, an empty environment plus the given variables.
render() {
  local file="$1" out="$2"; shift 2
  local envf="$TMP/render.env"
  : > "$envf"
  (( $# )) && printf '%s\n' "$@" > "$envf"
  env -i PATH="$PATH" HOME="$HOME" $DOCKER compose -f "$COMPOSE_DIR/$file" \
    --env-file "$envf" --profile '*' config --format json > "$out"
}

# value <json> <role> <variable> -> the variable's value, "<unset>", or "<no proxy>"
value() {
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
services = json.load(open(sys.argv[1]))["services"]
role, var = sys.argv[2], sys.argv[3]
def env(svc):
    e = svc.get("environment") or {}
    if isinstance(e, list):
        e = dict(item.split("=", 1) if "=" in item else (item, None) for item in e)
    return e
if role == "app":
    found = [s for s in services.values() if "SANDBOX_MODE" in env(s)]
    assert len(found) == 1, f"expected one app service with SANDBOX_MODE, found {len(found)}"
    svc = found[0]
else:
    svc = services.get("docker-socket-proxy")
    if svc is None:
        print("<no proxy>")
        sys.exit(0)
v = env(svc).get(var)
print("<unset>" if v is None else v)
PY
}

fail=0
check() { # check <name> <expected> <actual>
  if [[ "$2" == "$3" ]]; then echo "ok   $1"; else echo "FAIL $1: expected '$2', got '$3'"; fail=1; fi
}

for f in "${FILES[@]}"; do
  overrides=()
  for spec in "${VARS[@]}"; do
    IFS='|' read -r var _default override _where <<<"$spec"
    overrides+=("$var=$override")
  done
  render "$f" "$TMP/default.json"
  render "$f" "$TMP/override.json" "${overrides[@]}"
  for spec in "${VARS[@]}"; do
    IFS='|' read -r var default override where <<<"$spec"
    for role in $where; do
      got="$(value "$TMP/default.json" "$role" "$var")"
      [[ "$got" == "<no proxy>" ]] && continue
      check "$f $role $var default" "$default" "$got"
      check "$f $role $var override" "$override" "$(value "$TMP/override.json" "$role" "$var")"
    done
  done
done

exit "$fail"
