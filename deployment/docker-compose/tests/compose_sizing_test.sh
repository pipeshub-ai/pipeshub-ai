#!/usr/bin/env bash
# Datastore memory sizing in the Compose files (needs `docker compose config` only; starts nothing).
#  1. With an empty environment the sizing values equal the literals they replaced.
#  2. The new variables take effect.
#  3. An empty value falls back to the default.
#  4. An override file that sets the existing neo4j key names still wins.
# Set DOCKER="sudo -n docker" if the daemon socket needs it.
set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# render <compose-file> <out.json> [VAR=value...]: renders with an empty environment plus the given variables.
render() {
  local file="$1" out="$2"; shift 2
  local envf="$TMP/render.env"
  : > "$envf"
  (( $# )) && printf '%s\n' "$@" > "$envf"
  env -i PATH="$PATH" HOME="$HOME" $DOCKER compose -f "$COMPOSE_DIR/$file" ${EXTRA_FILE:+-f "$EXTRA_FILE"} \
    --env-file "$envf" --profile graph-neo4j config --format json > "$out"
}

# summarize <json> -> one line per sizing fact
summarize() {
  python3 - "$1" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))["services"]
def mem(s, kind):
    r = c[s].get("deploy", {}).get("resources", {}).get(kind, {})
    return r.get("memory", "-")
neo = {e.split("=", 1)[0]: e.split("=", 1)[1] for e in
       (c["neo4j"]["environment"] if isinstance(c["neo4j"]["environment"], list)
        else [f"{k}={v}" for k, v in c["neo4j"]["environment"].items()])} \
      if isinstance(c["neo4j"]["environment"], list) else c["neo4j"]["environment"]
for k in ("NEO4J_dbms_memory_heap_initial__size", "NEO4J_dbms_memory_heap_max__size",
          "NEO4J_dbms_memory_pagecache_size"):
    print(k, neo.get(k))
for s in ("neo4j", "mongodb", "qdrant"):
    if s in c:
        print(s, "limit", mem(s, "limits"), "reservation", mem(s, "reservations"))
PY
}

fail=0
check() { # check <name> <expected> <actual>
  if [[ "$2" == "$3" ]]; then echo "ok   $1"; else echo "FAIL $1"; diff <(echo "$2") <(echo "$3") || true; fail=1; fi
}

GiB=1073741824; MiB=1048576
for f in docker-compose.yml docker-compose.build.neo4j.yml; do
  render "$f" "$TMP/default.json"
  got="$(summarize "$TMP/default.json" | grep -E '^(NEO4J_|neo4j|qdrant)')"
  want="NEO4J_dbms_memory_heap_initial__size 1G
NEO4J_dbms_memory_heap_max__size 2G
NEO4J_dbms_memory_pagecache_size 1G
neo4j limit $((3*GiB)) reservation $((2*GiB))
qdrant limit $(( $( [[ $f == docker-compose.yml ]] && echo 6 || echo 4 ) *GiB)) reservation $((2*GiB))"
  check "$f defaults" "$want" "$got"
  if [[ $f == docker-compose.yml ]]; then
    check "$f mongo defaults" "mongodb limit $((2*GiB)) reservation $((512*MiB))" \
      "$(summarize "$TMP/default.json" | grep '^mongodb')"
  fi

  render "$f" "$TMP/set.json" \
    NEO4J_HEAP_INITIAL=256M NEO4J_HEAP_MAX=1536M NEO4J_PAGECACHE=128M \
    NEO4J_MEMORY_LIMIT=2304M NEO4J_MEMORY_RESERVATION=1G QDRANT_MEMORY_RESERVATION=512M \
    MONGO_MEMORY_RESERVATION=256M
  got="$(summarize "$TMP/set.json" | grep -E '^(NEO4J_|neo4j|qdrant)')"
  want="NEO4J_dbms_memory_heap_initial__size 256M
NEO4J_dbms_memory_heap_max__size 1536M
NEO4J_dbms_memory_pagecache_size 128M
neo4j limit $((2304*MiB)) reservation $GiB
qdrant limit $(( $( [[ $f == docker-compose.yml ]] && echo 6 || echo 4 ) *GiB)) reservation $((512*MiB))"
  check "$f variables take effect" "$want" "$got"
  if [[ $f == docker-compose.yml ]]; then
    check "$f mongo reservation takes effect" "mongodb limit $((2*GiB)) reservation $((256*MiB))" \
      "$(summarize "$TMP/set.json" | grep '^mongodb')"
  fi

  # An empty value means unset: it falls back to the default instead of rendering an invalid size.
  render "$f" "$TMP/empty.json" NEO4J_MEMORY_LIMIT= NEO4J_HEAP_MAX=
  check "$f empty value uses the default" "NEO4J_dbms_memory_heap_max__size 2G
neo4j limit $((3*GiB)) reservation $((2*GiB))" \
    "$(summarize "$TMP/empty.json" | grep -E '^(NEO4J_dbms_memory_heap_max|neo4j)')"

  # An override file using the existing key names still wins over the base file.
  cat > "$TMP/override.yml" <<'YML'
services:
  neo4j:
    environment:
      - NEO4J_dbms_memory_heap_max__size=3G
YML
  EXTRA_FILE="$TMP/override.yml" render "$f" "$TMP/ovr.json"
  check "$f override file wins" "NEO4J_dbms_memory_heap_max__size 3G" \
    "$(summarize "$TMP/ovr.json" | grep heap_max)"
done

(( fail == 0 )) && echo "compose_sizing_test: PASS" || { echo "compose_sizing_test: FAIL"; exit 1; }
