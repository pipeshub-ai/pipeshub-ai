#!/usr/bin/env bash
# ==============================================================================
# Node's logging in the app image, run as the Helm chart and Compose run it
# (D3, CT-1..CT-5). Opt-in Docker lane:
#
#   PXH_APP_IMAGE=<image built from this tree> bash deployment/tests/node_logging_container.sh
#
# Each case logs 2,000 lines through the image's own Node logger and asserts
# every one reaches stdout in time. Containers carry a per-run label and are
# removed by that label only (the Docker daemon may be shared).
# ==============================================================================
set -uo pipefail

IMAGE=${PXH_APP_IMAGE:?set PXH_APP_IMAGE to the app image to test}
LINES=2000
RUN="pxh-d3-$$-$RANDOM"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/pxh-d3.XXXXXX")
chmod 755 "$WORK"
# shellcheck disable=SC2317 # run by the trap
cleanup() {
  docker ps -aq --filter "label=pxh.test.run=$RUN" | xargs -r docker rm -f >/dev/null 2>&1
  rm -rf "$WORK"
}
trap cleanup EXIT

cat >"$WORK/emit.js" <<'EOF'
const { Logger } = require('/app/backend/dist/libs/services/logger.service');
const n = Number(process.argv[2]);
const logger = new Logger({ service: 'd3-container-test' });
for (let i = 0; i < n; i += 1) logger.info(`d3 line ${i}`);
setTimeout(() => process.exit(0), 2000);
EOF

COMMON=(--rm --label pipeshub.harness.owner=x-lead --label "pxh.test.run=$RUN" -v "$WORK:/probe:ro")
# values.yaml: podSecurityContext runAsUser/runAsGroup/fsGroup 1000; container
# securityContext drops ALL capabilities, no privilege escalation; HOME,
# /app/python/logs and /root/.local are emptyDirs.
CHART=(--user 1000:1000 --group-add 1000 --cap-drop ALL --security-opt no-new-privileges
  --tmpfs "/home/user:uid=1000,gid=1000" --tmpfs "/app/python/logs:uid=1000,gid=1000"
  --tmpfs "/root/.local:uid=1000,gid=1000"
  -e HOME=/home/user -e NODE_ENV=production -e LOG_LEVEL=info -e PORT=3001 -e NODEJS_PORT=3001)

failed=0
check() { # name, detail, true|false
  if [[ "$3" == true ]]; then echo "ok   $1 — $2"; else echo "FAIL $1 — $2"; failed=1; fi
}

emitted() { grep -c 'd3 line' "$1" || true; }
in_order() { [[ "$(grep 'd3 line' "$1" | tail -1 | sed 's/.* - //')" == "d3 line $((LINES - 1))" ]]; }

run_node() { # name, extra docker args...
  local name=$1
  shift
  timeout 60 docker run "${COMMON[@]}" --name "$RUN-$name" "$@" -w /app/backend --entrypoint bash "$IMAGE" \
    -c "node /probe/emit.js $LINES; echo; echo LOGFILES:; ls /app/backend | grep '\.log$' || true" \
    >"$WORK/$name.out" 2>&1
}

run_node ct1 "${CHART[@]}"
n=$(emitted "$WORK/ct1.out")
in_order "$WORK/ct1.out"; ordered=$?
files=$(sed -n '/^LOGFILES:/,$p' "$WORK/ct1.out" | grep -c '\.log$' || true)
ok=true; [[ $n == "$LINES" && $ordered == 0 && $files == 0 ]] || ok=false
check CT-1 "chart uid: $n/$LINES lines on stdout, $files log files" "$ok"

run_node ct2 "${CHART[@]}" -e NODE_LOG_FILES=true -e NODE_LOG_DIR=/app/backend
n=$(emitted "$WORK/ct2.out")
failures=$(grep -c 'logger.sink_failed' "$WORK/ct2.out" || true)
ok=true; [[ $n == "$LINES" && $failures -ge 1 ]] || ok=false
check CT-2 "unwritable NODE_LOG_DIR: $n/$LINES lines, $failures sink_failed reports" "$ok"

timeout 60 docker run "${COMMON[@]}" --name "$RUN-ct3" "${CHART[@]}" --tmpfs "/var/log/pipeshub/node:uid=0,gid=1000,mode=0770" \
  -e NODE_LOG_FILES=true -e NODE_LOG_DIR=/var/log/pipeshub/node -w /app/backend --entrypoint bash "$IMAGE" \
  -c "node /probe/emit.js $LINES; echo COMBINED=\$(grep -c 'd3 line' /var/log/pipeshub/node/combined.log)" \
  >"$WORK/ct3.out" 2>&1
n=$(emitted "$WORK/ct3.out")
combined=$(sed -n 's/^COMBINED=//p' "$WORK/ct3.out")
ok=true; [[ $n == "$LINES" && $combined == "$LINES" ]] || ok=false
check CT-3 "fsGroup-writable volume: $n/$LINES on stdout, ${combined:-0}/$LINES in combined.log" "$ok"

run_node ct4
n=$(emitted "$WORK/ct4.out")
files=$(sed -n '/^LOGFILES:/,$p' "$WORK/ct4.out" | grep -c '\.log$' || true)
ok=true; [[ $n == "$LINES" && $files == 0 ]] || ok=false
check CT-4 "as Compose (root): $n/$LINES lines on stdout, $files log files in /app/backend" "$ok"

docker run -d "${COMMON[@]:1}" --name "$RUN-ct5" "${CHART[@]}" "$IMAGE" >/dev/null
for _ in $(seq 1 30); do
  docker logs "$RUN-ct5" 2>&1 | grep -q 'Node.js started with PID' && break
  sleep 1
done
docker logs "$RUN-ct5" >"$WORK/ct5.out" 2>&1
running=$(docker inspect -f '{{.State.Running}}' "$RUN-ct5" 2>/dev/null)
denied=$(grep -c 'Permission denied' "$WORK/ct5.out" || true)
started=$(grep -c 'Node.js started with PID' "$WORK/ct5.out" || true)
ok=true; [[ $started -ge 1 && $running == true && $denied == 0 ]] || ok=false
check CT-5 "process monitor as chart uid: running=$running, node started=$started, permission denied=$denied" "$ok"

if [[ $failed != 0 ]]; then
  for f in "$WORK"/*.out; do echo "--- $(basename "$f")"; tail -5 "$f"; done
fi
exit "$failed"
