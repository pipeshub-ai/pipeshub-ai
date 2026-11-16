#!/usr/bin/env bash
# Every capacity knob blank (as compose's KEY=${KEY:-} and a Helm value of "" deliver it): each
# Python service's start-up import must succeed, log `capacity: service=X omp=N`, and leave the
# blank knobs unset so an int() read of one cannot crash.
#
#   IMAGE=<image> bash capacity_blank_env_test.sh     # runs inside the image (CI)
#   CAPACITY_TEST_LOCAL=1 bash capacity_blank_env_test.sh   # runs from backend/python with the venv on PATH
#
# Without IMAGE or CAPACITY_TEST_LOCAL it fails: a missing image is not a pass.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"

if [ -z "${IMAGE:-}" ] && [ -z "${CAPACITY_TEST_LOCAL:-}" ]; then
  echo "capacity_blank_env_test: set IMAGE (CI) or CAPACITY_TEST_LOCAL=1" >&2
  exit 2
fi

# The knob names come from the registry, so a new knob is covered without editing this script.
knobs="$(cd "$repo/backend/python" && python -c 'from app.capacity.knobs import KNOBS; print(" ".join(k.name for k in KNOBS))')"
services="indexing query connectors embedding docling parsing extraction"

run() { # run <service> -- python code on stdin
  local svc="$1" code="$2" args=() largs=() k
  for k in $knobs; do args+=(-e "$k="); largs+=("$k="); done
  if [ -n "${IMAGE:-}" ]; then
    docker run --rm "${args[@]}" -e PIPESHUB_SERVICE="$svc" -e USE_PARSING_SERVICE=true -w /app/python "$IMAGE" python -W ignore -c "$code" 2>&1
  else
    ( cd "$repo/backend/python"; env "${largs[@]}" PIPESHUB_SERVICE="$svc" USE_PARSING_SERVICE=true python -W ignore -c "$code" 2>&1 )
  fi
}

fail=0
for svc in $services; do
  out="$(run "$svc" 'import app.utils.runtime_threads, os
from app.utils.concurrency import MAX_CONCURRENT_INDEXING_LLM_CALLS
from app.utils.indexing_workers import indexing_worker_count
assert "OMP_NUM_THREADS" in os.environ and os.environ["OMP_NUM_THREADS"].strip()
assert indexing_worker_count() == 1
print("ok", MAX_CONCURRENT_INDEXING_LLM_CALLS)')" || { echo "FAIL $svc: $out"; fail=1; continue; }
  grep -q "^capacity: service=$svc omp=2 " <<<"$out" || { echo "FAIL $svc: no capacity line in: $out"; fail=1; continue; }
  grep -q "^ok 24$" <<<"$out" || { echo "FAIL $svc: $out"; fail=1; continue; }
  # The service's own entry module must import with every knob blank, and with no runtime_threads import
  # of ours in front of it: a read of "" before the entry point's own first import would crash here.
  if [ -f "$repo/backend/python/app/${svc}_main.py" ]; then
    out="$(run "$svc" "import app.${svc}_main; print('entry ok')")" || { echo "FAIL $svc entry module: $out"; fail=1; continue; }
    grep -q "^entry ok$" <<<"$out" || { echo "FAIL $svc entry module: $out"; fail=1; continue; }
  fi
  echo "ok $svc"
done
exit $fail
