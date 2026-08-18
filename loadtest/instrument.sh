#!/usr/bin/env bash
# instrument.sh on|off|status — per-phase and per-backend timing in the
# query service.
#
# `perftest.sh` reports throughput, turn-phase latency and backend latency from
# log lines this adds. CPU and memory work without it; the rest needs it on.
#
# Nothing under backend/ is edited: the probes are copied into the running
# container and imported from `app/utils/runtime_threads.py`, which
# `query_main` imports first. `docker restart` keeps them; `docker compose up`
# recreates the container and drops them — re-run `on` after that.
set -uo pipefail
ACTION=${1:-status}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINER=${PIPESHUB_CONTAINER:-pipeshub-ai}
RT=/app/python/app/utils/runtime_threads.py
# shellcheck source=_common.sh
. "$HERE/_common.sh"
require_target || exit 1
if [ "$PIPESHUB_MODE" != "docker" ]; then
    echo "instrument.sh needs PIPESHUB_MODE=docker — it copies probes into the"
    echo "container and patches files there. A native deployment would mean"
    echo "editing your working tree, which this script will not do."
    echo
    echo "Native runs still report CPU and memory; throughput, phase latency and"
    echo "backend latency are the sections that need these probes."
    exit 1
fi

# -i is required: the heredocs below pipe a script into `python3 -`, and without
# stdin attached docker exec hands python an immediate EOF. It then executes
# nothing, prints nothing and exits 0 — so `on` reported success while the probe
# was never wired, and `off` never removed it.
in_container() { $DOCKER exec -i "$CONTAINER" "$@"; }

case "$ACTION" in
  on)
    echo "== copying probes into $CONTAINER"
    $DOCKER cp "$HERE/instr/query_phase_timing.py" "$CONTAINER:/app/python/app/utils/query_phase_timing.py"
    $DOCKER cp "$HERE/instr/backend_timing.py"     "$CONTAINER:/app/python/app/utils/backend_timing.py"
    $DOCKER cp "$HERE/instr/apply_instrumentation.py" "$CONTAINER:/tmp/apply_instrumentation.py"

    echo "== patching phase marks into the request path"
    # The patcher takes both request-path targets; called bare it prints usage
    # and exits 2, which meant the phase patch silently never applied.
    in_container python3 /tmp/apply_instrumentation.py \
      /app/python/app/api/routes/chatbot.py \
      /app/python/app/agents/chat_modes/bridge.py || {
      echo "!! phase patch failed — throughput/latency will be unavailable"; }

    echo "== wiring the backend-latency probe"
    in_container python3 - <<'PY'
p = "/app/python/app/utils/runtime_threads.py"
src = open(p).read()
line = "    from app.utils import backend_timing as _backend_timing  # noqa: F401"
block = "try:\n" + line + "\nexcept Exception:\n    pass\n"
if "backend_timing" in src:
    print("   already wired")
else:
    lines = src.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.startswith("def ") or ln.startswith("class "):
            lines.insert(i, block + "\n")
            break
    else:
        # runtime_threads.py is pure module-level setup with no def/class to
        # insert before. The loop used to fall through, write the file back
        # byte-identical and still print "wired" — so the probe never loaded
        # and every run reported "no BACKENDAGG lines" as if uninstrumented.
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append("\n" + block)
    out = "".join(lines)
    compile(out, p, "exec")
    open(p, "w").write(out)
    if "backend_timing" not in open(p).read():
        raise SystemExit("!! backend probe wiring failed — backend latency unavailable")
    print("   wired")
PY
    bash "$HERE/restart_query.sh"
    ;;

  off)
    echo "== removing probes"
    in_container python3 - <<'PY'
p = "/app/python/app/utils/runtime_threads.py"
src = open(p).read()
# Drop the whole injected block. Filtering only the lines mentioning the module
# left a bare `try:` / `except Exception:` behind -- invalid Python, which the
# compile() below then rejected, so `off` failed and left the probe wired.
block = (
    "try:\n"
    "    from app.utils import backend_timing as _backend_timing  # noqa: F401\n"
    "except Exception:\n"
    "    pass\n"
    "\n"
)
if block in src:
    out = src.replace(block, "")
elif "backend_timing" not in src:
    print("   not wired")
    raise SystemExit(0)
else:
    print("   !! probe block not in its expected shape -- leaving the file alone")
    raise SystemExit(1)
compile(out, p, "exec")
open(p, "w").write(out)
print("   unwired")
PY
    in_container sh -c 'rm -f /app/python/app/utils/backend_timing.py' || true
    in_container sh -c 'rm -f /app/python/app/utils/query_phase_timing.py' || true
    # The patcher is forward-only. Phase marks stay until the container is
    # recreated; say so rather than pretending a revert ran.
    echo "   (phase marks remain — recreate the container to clear them)"
    bash "$HERE/restart_query.sh"
    ;;

  status)
    echo -n "phase marks : "
    in_container grep -qc "_phase_mark" /app/python/app/api/routes/chatbot.py 2>/dev/null \
      && echo "applied" || echo "not applied"
    echo -n "backend probe: "
    in_container grep -q "backend_timing" "$RT" 2>/dev/null && echo "wired" || echo "not wired"
    echo -n "recent lines : "
    n=$($DOCKER logs "$CONTAINER" --since 10m 2>&1 | grep -ac 'PHASE \|BACKENDAGG' || true)
    echo "$n in the last 10 min"
    ;;

  *)
    echo "usage: ./instrument.sh on|off|status"; exit 1 ;;
esac
