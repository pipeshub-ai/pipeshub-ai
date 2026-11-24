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
  "DOCKER_PROXY_INSTANCE|compose:sandbox-env-test|stack-a|proxy"
  "DOCKER_PROXY_REAP_UNLABELED|false|true|proxy"
  "SANDBOX_MAX_TOTAL||7|app"
  "SANDBOX_MAX_PER_ORG||3|app"
  "SANDBOX_MAX_PER_REQUEST||4|app"
  "SANDBOX_EGRESS_DNS_SERVERS||10.0.0.53|app"
  "SANDBOX_MIN_TOTAL||2|app"
  "SANDBOX_MEMORY_SHARE||0.5|app"
  "SANDBOX_CPU_FACTOR||1.0|app"
  "SANDBOX_SLOT_WAIT_S||30|app"
  "SANDBOX_HOST_MEMORY_BYTES||17179869184|app"
  "SANDBOX_HOST_CPU_MILLIS||4000|app"
  "SANDBOX_LIVE_LIMIT||off|app"
  "SANDBOX_LIVE_INTERVAL_S||30|app"
  "SANDBOX_HOST_MEM_FLOOR_MB||2048|app"
  "SANDBOX_HOST_MEM_SOFT_MB||4096|app"
  "SANDBOX_HOST_CPU_BRAKE||0.8|app"
  "SANDBOX_HOST_CPU_PSI_BRAKE||0.6|app"
  "SANDBOX_MEMORY_OVERCOMMIT||2|app"
  "SANDBOX_YIELD_INDEXING||off|app"
  "SANDBOX_YIELD_TTL_S||30|app"
  "SANDBOX_ALERTS||false|app"
  "SANDBOX_ALERT_WINDOW_S||600|app"
  "SANDBOX_ALERT_EVAL_S||30|app"
  "SANDBOX_ALERT_WARN_BUSY_PCT||50|app"
  "SANDBOX_ALERT_WARN_MIN_WAITED||10|app"
  "SANDBOX_ALERT_CRIT_MIN_REFUSED||5|app"
  "SANDBOX_ALERT_CRIT_REFUSED_PCT||10|app"
  "SANDBOX_ALERT_COOLDOWN_S||3600|app"
  "SANDBOX_ALERT_SCOPE||host-a|app"
  "SANDBOX_STATE_DIR||/srv/sandbox-state|app"
  "SANDBOX_DISK_WATCHDOG||on|app"
  "SANDBOX_DISK_WATCH_CHECK_TIMEOUT_S||20|app"
  "SANDBOX_DISK_WATCH_MAX_FAILURES||5|app"
  "SANDBOX_LOG_MAX_SIZE||20m|app"
  "SANDBOX_LOG_MAX_FILES||2|app"
  "SANDBOX_STDIO_HEAD_BYTES||1000|app"
  "SANDBOX_STDIO_TAIL_BYTES||500|app"
  "SANDBOX_OUTPUT_MAX_FILE_BYTES||1000000|app"
  "SANDBOX_OUTPUT_MAX_TOTAL_BYTES||5000000|app"
  "SANDBOX_OUTPUT_MAX_FILES||100|app"
  "SANDBOX_OUTPUT_MAX_SANDBOX_BYTES||9000000|app"
  "SANDBOX_OUTPUT_MAX_SANDBOX_FILES||200|app"
  "SANDBOX_DEPS_MAX_TOTAL_BYTES||100000000|app"
)

# render <compose-file> <out.json> [VAR=value...]: every profile, an empty environment plus the given variables,
# under a fixed project name so defaults derived from it are known.
render() {
  local file="$1" out="$2"; shift 2
  local envf="$TMP/render.env"
  # The legacy file has no fallback for these secrets, so every render gets placeholders.
  printf '%s=test\n' SECRET_KEY ARANGO_PASSWORD MONGO_PASSWORD QDRANT_API_KEY > "$envf"
  (( $# )) && printf '%s\n' "$@" >> "$envf"
  env -i PATH="$PATH" HOME="$HOME" $DOCKER compose -p sandbox-env-test -f "$COMPOSE_DIR/$file" \
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

# The proxy defaults to loopback, so Compose must set its address; it may only
# listen on all interfaces because it publishes no port and sits only on
# internal networks.
for f in "${FILES[@]}"; do
  render "$f" "$TMP/default.json"
  got="$(python3 - "$TMP/default.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
svc = cfg["services"].get("docker-socket-proxy")
if svc is None:
    print("<no proxy>")
    sys.exit(0)
env = svc.get("environment") or {}
if isinstance(env, list):
    env = dict(item.split("=", 1) for item in env if "=" in item)
nets = svc.get("networks") or {}
internal = all((cfg.get("networks") or {}).get(n, {}).get("internal") for n in nets)
print(f"host={env.get('DOCKER_PROXY_HOST')} ports={bool(svc.get('ports'))} internal={bool(nets) and internal}")
PY
)"
  [[ "$got" == "<no proxy>" ]] && continue
  check "$f proxy listen address" "host=0.0.0.0 ports=False internal=True" "$got"
done

exit "$fail"
