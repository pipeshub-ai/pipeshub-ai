#!/usr/bin/env bash
# Run the parse benchmark inside docker cgroup limits, one container per tier,
# and print the targets table. Needs a Docker daemon; an agent sandbox has none.
#
#   backend/python/tests/perf/parse_bench/run_tiers.sh [--dry-run]
#
# Environment (all optional):
#   BENCH_IMAGE   image with the app's Python dependencies (default pipeshubai/pipeshub-ai:latest)
#   BENCH_TIERS   "label:cpus:memGB ..." (default "T-small:2:4 T-laptop:4:8 T-server:16:32")
#   BENCH_OUT     results directory (default notes-style ./bench-results/<date>)
#   BENCH_CORPUS  corpus directory (default ./bench-corpus; generated with the standard profile if missing)
#   BENCH_REPEATS runs per tier and mix (default 3; the median is reported)
#   BENCH_MIXES   default "cpu-heavy io-heavy laptop-typical"
#   BENCH_GOLDEN  golden file to check the first run of each cell against
#   BENCH_MOUNT_APP  1 (default) runs the checkout's app code in the image; 0 runs the image's
#
# T-server needs a host with at least 16 CPUs and about 36 GB free; a tier that does not fit
# is skipped with a message. T-big (64 / 128 GB) is not in the default list.
set -euo pipefail

dry=0
[[ ${1:-} == --dry-run ]] && dry=1

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
python_root=$(cd "$here/../../.." && pwd)
image=${BENCH_IMAGE:-pipeshubai/pipeshub-ai:latest}
tiers=${BENCH_TIERS:-"T-small:2:4 T-laptop:4:8 T-server:16:32"}
out=$(realpath -m "${BENCH_OUT:-./bench-results/$(date +%Y%m%d-%H%M)}")
corpus=$(realpath -m "${BENCH_CORPUS:-./bench-corpus}")
repeats=${BENCH_REPEATS:-3}
mixes=${BENCH_MIXES:-"cpu-heavy io-heavy laptop-typical"}
host_cpus=$(nproc)
host_mem_gb=$(awk '/MemTotal/{printf "%d", $2/1024/1024}' /proc/meminfo)

run() { if ((dry)); then printf '%q ' "$@"; echo; else "$@"; fi; }

if [[ ! -f $corpus/manifest.json ]]; then
  echo "generating the standard corpus in $corpus"
  run env PYTHONPATH="$python_root" python3 -m tests.perf.parse_bench.corpus --out "$corpus"
fi
run mkdir -p "$out"

mount_app=()
if [[ ${BENCH_MOUNT_APP:-1} == 1 ]]; then
  mount_app=(-v "$python_root/app:/app/python/app:ro")
fi
golden_args=()
golden_mount=()
if [[ -n ${BENCH_GOLDEN:-} ]]; then
  golden_mount=(-v "$(realpath "$BENCH_GOLDEN"):/golden.json:ro")
  golden_args=(--golden-check /golden.json)
fi

limits=()
for tier in $tiers; do
  IFS=: read -r label cpus mem <<<"$tier"
  limits+=(--tier "$label=$mem")
  if ((cpus > host_cpus || mem + 4 > host_mem_gb)); then
    echo "skip $label: needs $cpus CPUs and $((mem + 4)) GB, this host has $host_cpus and $host_mem_gb"
    continue
  fi
  echo "== $label: --cpus $cpus --memory ${mem}g"
  # Swap off (memory-swap = memory) so memory.peak is the real working set.
  run docker run --rm --name "parsebench-$label" \
    --cpus "$cpus" --memory "${mem}g" --memory-swap "${mem}g" \
    --user 0 --entrypoint python \
    -e PYTHONPATH=/app/python:/bench -e HF_HUB_OFFLINE=1 \
    -v "$python_root/tests/perf/parse_bench:/bench/tests/perf/parse_bench:ro" \
    "${mount_app[@]}" "${golden_mount[@]}" \
    -v "$corpus:/corpus:ro" -v "$out/$label:/out" \
    "$image" -m tests.perf.parse_bench.matrix run \
      --corpus /corpus --out-dir /out --label "$label" --repeats "$repeats" \
      --mixes $mixes "${golden_args[@]}"
done

echo "== summary"
args=()
for tier in $tiers; do
  label=${tier%%:*}
  [[ -d $out/$label ]] && args+=(--out-dir "$out/$label")
done
((dry)) && { echo "(dry run: nothing executed)"; exit 0; }
PYTHONPATH="$python_root" python3 -m tests.perf.parse_bench.matrix summarize "${args[@]}" "${limits[@]}" | tee "$out/summary.md"
echo "results: $out"
