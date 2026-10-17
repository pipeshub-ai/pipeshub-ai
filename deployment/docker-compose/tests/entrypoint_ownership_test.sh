#!/usr/bin/env bash
# ==============================================================================
# Tests for the ownership pass in deployment/docker-entrypoint.sh.
# ==============================================================================
# The entrypoint chowns data volumes as root, so the cases run inside a small
# throwaway container (the app's base image) against the real volume paths.
# A `find` shim on PATH records every invocation, which is how a test tells a
# full pass (it carries -exec) from the cheap marker check, and it can make the
# pass fail or be killed part-way.
#
# Covers:
#   - a volume left by a root-running release: everything handed to 1000:1000,
#     symlinks changed without following them, the marker written, and the
#     command run as 1000 with no capabilities and no_new_privs;
#   - the next start skips the full pass;
#   - a pass killed part-way leaves no marker, and the next start finishes it;
#   - a pass that fails does not block startup and is retried, and a real
#     failure (a read-only mount) is logged with the entry and the reason;
#   - root-owned files after the marker: a volume restored as root (found by
#     the top-level check), the docker-compose.yml fallback for older images
#     and process_monitor.sh run as root (both drop the markers);
#   - a marker the runtime user created, or a hard link to a root-owned
#     leftover, is not trusted; a planted marker symlink does not make root
#     write outside the volume;
#   - -xdev: a nested mount is neither changed nor makes the check walk again;
#   - a start as uid 1000 just execs.
#
# Run: bash deployment/docker-compose/tests/entrypoint_ownership_test.sh [entrypoint]
#   entrypoint defaults to deployment/docker-entrypoint.sh; pass another copy
#   to see which cases it fails. Needs docker and docker compose.
# ==============================================================================
# check() evals its test string, so the single-quoted expansions are deliberate.
# shellcheck disable=SC2016,SC2034,SC2317
set -uo pipefail

INSIDE_DIR=/opt/entrypoint-test
VOLUMES=(/data/pipeshub /root/.local /root/.cache/huggingface)
MARKER=.pipeshub-ownership-v1

if [[ "${1:-}" == "--inside" ]]; then
  FAILS=0
  pass() { printf "  ok   - %s\n" "$1"; }
  fail() { printf "  FAIL - %s\n" "$1"; FAILS=$((FAILS + 1)); }
  check() { if eval "$2"; then pass "$1"; else fail "$1"; fi; } # check "desc" 'test'

  ENTRYPOINT="$INSIDE_DIR/docker-entrypoint.sh"
  CALLS=/tmp/find-calls

  mkdir -p "${VOLUMES[@]}"
  chown 1000:1000 /root # as the Dockerfile does: the runtime user's home
  # The shim sits ahead of /usr/bin on PATH. FIND_MODE=fail fails every full
  # pass; FIND_MODE=interrupt:<volume> fixes that volume's top two levels (what
  # the first -exec batches reach) and then kills the entrypoint's process group.
  cat >/usr/local/sbin/find <<'SHIM'
#!/bin/sh
echo "$*" >>/tmp/find-calls
case " $* " in *" -exec "*)
  case "${FIND_MODE:-}" in
    fail) exit 1 ;;
    "interrupt:$1")
      /usr/bin/find "$1" -xdev -maxdepth 2 -exec chown -h 1000:1000 {} +
      kill -KILL 0 ;;
  esac ;;
esac
exec /usr/bin/find "$@"
SHIM
  chmod 755 /usr/local/sbin/find

  cat >/tmp/probe.sh <<'PROBE'
#!/bin/sh
grep -E '^(Uid|Gid|CapEff|CapBnd|NoNewPrivs):' /proc/self/status >/tmp/probe.out
PROBE
  chmod 755 /tmp/probe.sh

  # Mimics what a root-running release leaves: local-storage records, a model,
  # and a symlink that must be changed itself, not followed.
  seed_root_owned() {
    for v in "${VOLUMES[@]}"; do
      /usr/bin/find "$v" -xdev -mindepth 1 -maxdepth 1 ! -name nested -exec rm -rf {} +
    done
    local docs=/root/.local/PipesHub/org1/PipesHub
    for i in $(seq 1 40); do
      mkdir -p "$docs/doc$i/current"
      echo data >"$docs/doc$i/current/report.pdf"
    done
    mkdir -p /root/.cache/huggingface/hub/models--m/blobs /data/pipeshub/state
    echo w >/root/.cache/huggingface/hub/models--m/blobs/b1
    echo s >/data/pipeshub/state/s1
    ln -sfn /etc/passwd /root/.local/passwd-link
    chown -R 0:0 "${VOLUMES[@]}"
    echo nested >/data/pipeshub/nested/inside
    chown 0:0 /data/pipeshub/nested/inside
  }

  start() { # start [env...]: one container start as root, in its own process group
    rm -f /tmp/probe.out; : >"$CALLS"
    # The subshell keeps bash's "Killed" notice for the interrupt case quiet.
    (env "$@" setsid -w sh "$ENTRYPOINT" /tmp/probe.sh 2>/tmp/entrypoint.err) 2>/dev/null
  }
  full_passes() { grep -c -- ' -exec ' "$CALLS"; }
  # Entries not owned by the app, other than the root-owned markers and the
  # nested mount's contents.
  unowned() { /usr/bin/find "${VOLUMES[@]}" -xdev ! -name "$MARKER" \( ! -uid 1000 -o ! -gid 1000 \) -print; }
  markers_present() {
    local v; for v in "${VOLUMES[@]}"; do
      [[ -f "$v/$MARKER" && ! -L "$v/$MARKER" && "$(stat -c %u:%g:%h "$v/$MARKER")" == 0:0:1 ]] || return 1
    done
  }
  markers_absent() { local v; for v in "${VOLUMES[@]}"; do [[ ! -e "$v/$MARKER" ]] || return 1; done; }
  started_as_app() { grep -qE '^Uid:\s+1000\s' /tmp/probe.out 2>/dev/null; }
  finish() {
    echo
    if (( FAILS )); then echo "$FAILS check(s) failed"; exit 1; fi
    echo "all checks passed"; exit 0
  }

  if [[ "${2:-}" == readonly ]]; then
    echo "volume with a read-only mount whose root cannot be chowned"
    mkdir -p /root/.local/PipesHub && echo d >/root/.local/PipesHub/f && chown -R 0:0 /root/.local
    start
    check "startup is not blocked" 'started_as_app'
    check "log names the entry and the reason" 'grep -qF "/root/.cache/huggingface: ownership pass did not finish at /root/.cache/huggingface/ro (" /tmp/entrypoint.err && grep -qi "read-only file system" /tmp/entrypoint.err'
    check "no marker on that volume" '[[ ! -e /root/.cache/huggingface/$MARKER ]]'
    check "the other volumes are migrated" '[[ -f /root/.local/$MARKER && -z "$(/usr/bin/find /root/.local ! -name "$MARKER" \( ! -uid 1000 -o ! -gid 1000 \) -print)" ]]'
    finish
  fi

  echo "fresh volume left by a root-running release"
  seed_root_owned
  start
  check "every entry handed to 1000:1000" '[[ -z "$(unowned)" ]]'
  check "symlink changed without following it" '[[ "$(stat -c %u /etc/passwd)" == 0 && "$(stat -c %u /root/.local/passwd-link)" == 1000 ]]'
  check "marker written in each volume, root-owned, one link" 'markers_present'
  check "command runs as uid/gid 1000" 'started_as_app && grep -qE "^Gid:\s+1000\s" /tmp/probe.out'
  check "no capabilities, no_new_privs" 'grep -qE "^CapEff:\s+0+$" /tmp/probe.out && grep -qE "^CapBnd:\s+0+$" /tmp/probe.out && grep -qE "^NoNewPrivs:\s+1$" /tmp/probe.out'
  check "nested mount (-xdev) left alone" '[[ "$(stat -c %u /data/pipeshub/nested/inside)" == 0 ]]'

  echo "second start"
  start
  check "no full pass" '[[ "$(full_passes)" == 0 ]]'
  check "command still started" 'started_as_app'

  echo "pass killed part-way, after the marker had been invalidated"
  seed_root_owned
  touch /root/.local/$MARKER
  start FIND_MODE=interrupt:/root/.local
  check "command did not start" '! started_as_app'
  check "marker not left behind" '[[ ! -e /root/.local/$MARKER ]]'
  check "deeper entries still root-owned" '[[ -n "$(unowned)" ]]'
  start
  check "next start finishes the pass" '[[ -z "$(unowned)" ]] && markers_present && started_as_app'

  echo "pass that fails"
  seed_root_owned
  start FIND_MODE=fail
  check "startup is not blocked" 'started_as_app'
  check "no marker written" 'markers_absent'
  check "failure is logged" 'grep -q "did not finish" /tmp/entrypoint.err'
  start
  check "next start retries and completes" '[[ -z "$(unowned)" ]] && markers_present'

  echo "volume restored as root after the marker"
  chown -R 0:0 /root/.local
  start
  check "top-level check triggers a full pass" '[[ "$(full_passes)" == 1 ]]'
  check "all entries fixed, marker back" '[[ -z "$(unowned)" ]] && markers_present'

  echo "rollback to an image without the entrypoint (docker-compose.yml fallback)"
  mkdir -p /app
  printf '#!/bin/sh\nmkdir -p /root/.local/PipesHub/org1/PipesHub/rolled/current\necho x >/root/.local/PipesHub/org1/PipesHub/rolled/current/f\n' >/app/process_monitor.sh
  chmod 755 /app/process_monitor.sh
  sh -c "$(cat "$INSIDE_DIR/compose-entrypoint.sh")"
  check "fallback runs the image's process_monitor.sh as root" '[[ "$(stat -c %u /root/.local/PipesHub/org1/PipesHub/rolled/current/f)" == 0 ]]'
  check "fallback drops the markers" 'markers_absent'
  start
  check "next start fixes what the root run wrote" '[[ -z "$(unowned)" ]] && markers_present'

  echo "process_monitor.sh run as root (entrypoint bypassed)"
  timeout 20 bash "$INSIDE_DIR/process_monitor.sh" >/dev/null 2>&1
  check "process_monitor.sh drops the markers" 'markers_absent'
  mkdir -p /root/.local/PipesHub/org1/PipesHub/asroot && echo y >/root/.local/PipesHub/org1/PipesHub/asroot/f
  start
  check "next start fixes what the root run wrote" '[[ -z "$(unowned)" ]] && markers_present'
  markers_snapshot="$(stat -c %Y /root/.local/$MARKER 2>/dev/null)"
  timeout 20 setpriv --reuid=1000 --regid=1000 --clear-groups bash "$INSIDE_DIR/process_monitor.sh" >/dev/null 2>&1
  check "process_monitor.sh as uid 1000 keeps them" 'markers_present && [[ "$(stat -c %Y /root/.local/$MARKER)" == "$markers_snapshot" ]]'

  echo "marker created by the runtime user after a pass that did not finish"
  docs=/root/.local/PipesHub/org1/PipesHub
  mkdir -p "$docs/left/current" && echo z >"$docs/left/current/f"
  rm -f /root/.local/$MARKER
  setpriv --reuid=1000 --regid=1000 --clear-groups touch /root/.local/$MARKER
  start
  check "it is not trusted: full pass" '[[ "$(full_passes)" == 1 ]]'
  check "the leftover is fixed, a root marker replaces it" '[[ -z "$(unowned)" ]] && markers_present'

  echo "hard link to a root-owned leftover named as the marker"
  mkdir -p "$docs/linked" && echo l >"$docs/linked/f"
  rm -f /root/.local/$MARKER && ln "$docs/linked/f" /root/.local/$MARKER
  start
  check "it is not trusted: full pass, leftover fixed" '[[ "$(full_passes)" == 1 && -z "$(unowned)" ]] && markers_present'

  echo "planted marker symlink"
  seed_root_owned
  ln -s /etc/planted-by-app /root/.local/$MARKER
  start
  check "root wrote nothing outside the volume" '[[ ! -e /etc/planted-by-app ]]'
  check "the symlink is replaced by a real marker" 'markers_present'
  check "command still started" 'started_as_app'

  echo "start as uid 1000"
  seed_root_owned
  rm -f /tmp/probe.out; : >"$CALLS"
  setpriv --reuid=1000 --regid=1000 --clear-groups sh "$ENTRYPOINT" /tmp/probe.sh 2>/dev/null
  check "command runs" 'started_as_app'
  check "nothing scanned or changed" '[[ ! -s "$CALLS" ]] && [[ "$(stat -c %u /root/.local/PipesHub)" == 0 ]] && markers_absent'

  finish
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
ENTRYPOINT="$(realpath "${1:-$ROOT/deployment/docker-entrypoint.sh}")"
PROCESS_MONITOR="$ROOT/deployment/process_monitor.sh"
COMPOSE_DIR="$ROOT/deployment/docker-compose"
IMAGE="${ENTRYPOINT_TEST_IMAGE:-python:3.12-slim}"
NAME="${ENTRYPOINT_TEST_CONTAINER:-entrypoint-ownership-test-$$}"

TMP_ROOT="$(mktemp -d)"
trap 'docker rm -f -v "$NAME" >/dev/null 2>&1; rm -rf "$TMP_ROOT"' EXIT

echo "static checks"
STATIC_FAILS=0
if sh -n "$ENTRYPOINT"; then echo "  ok   - entrypoint parses as POSIX sh"
else echo "  FAIL - entrypoint parses as POSIX sh"; STATIC_FAILS=1; fi
# The marker paths are spelled out where the entrypoint cannot run; keep them in
# step with the volumes it walks.
for v in "${VOLUMES[@]}"; do
  grep -E '^for dir in' "$ENTRYPOINT" | grep -qF " $v" \
    || { echo "  FAIL - entrypoint walks $v"; STATIC_FAILS=1; }
  for f in "$PROCESS_MONITOR" "$COMPOSE_DIR/docker-compose.yml"; do
    grep -qF "$v/$MARKER" "$f" || { echo "  FAIL - ${f#"$ROOT"/} drops $v/$MARKER"; STATIC_FAILS=1; }
  done
done
(( STATIC_FAILS )) || echo "  ok   - marker paths match in the entrypoint, process_monitor.sh and docker-compose.yml"

# The app service's entrypoint as compose runs it, $$ unescaped.
docker compose -f "$COMPOSE_DIR/docker-compose.yml" --env-file "$COMPOSE_DIR/env.template" \
  config --format json >"$TMP_ROOT/compose.json" || { echo "docker compose config failed"; exit 1; }
python3 - "$TMP_ROOT/compose.json" >"$TMP_ROOT/compose-entrypoint.sh" <<'PY' || exit 1
import json, sys
entrypoint = json.load(open(sys.argv[1]))["services"]["pipeshub-ai"]["entrypoint"]
assert entrypoint[:2] == ["/bin/sh", "-c"], entrypoint
print(entrypoint[2].replace("$$", "$"))
PY

cp "$ENTRYPOINT" "$TMP_ROOT/docker-entrypoint.sh"
cp "$PROCESS_MONITOR" "$TMP_ROOT/process_monitor.sh"
cp "${BASH_SOURCE[0]}" "$TMP_ROOT/test.sh"
chmod -R a+rX "$TMP_ROOT"

docker run --rm --name "$NAME" --network none --memory 256m --cpus 1 --pids-limit 256 \
  --tmpfs /data/pipeshub/nested:rw,size=1m \
  -v "$TMP_ROOT:$INSIDE_DIR:ro" \
  "$IMAGE" bash "$INSIDE_DIR/test.sh" --inside
status=$?
docker run --rm --name "$NAME" --network none --memory 256m --cpus 1 --pids-limit 256 \
  --tmpfs /root/.cache/huggingface/ro:ro,size=1m \
  -v "$TMP_ROOT:$INSIDE_DIR:ro" \
  "$IMAGE" bash "$INSIDE_DIR/test.sh" --inside readonly || status=1
(( STATIC_FAILS )) && exit 1
exit "$status"
