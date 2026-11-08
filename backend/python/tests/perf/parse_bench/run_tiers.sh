#!/usr/bin/env bash
# Run the parse benchmark inside docker cgroup limits and print the targets table.
# Needs a Docker daemon on a cgroup v2 host; an agent sandbox has none.
#
#   backend/python/tests/perf/parse_bench/run_tiers.sh [--dry-run]
#
# One fresh container per tier, mix and repeat. That is what makes cgroup memory.peak the peak
# of exactly that run (it is the container's high-water mark since it started, so a container
# that ran several drivers would report a running maximum; resetting the file needs a writable
# cgroup mount, which Docker does not give by default). The peak includes the page cache of the
# corpus and the model files read, which is what the OOM killer counts too.
#
# Environment (all optional):
#   BENCH_IMAGE   image with the app's Python dependencies (default pipeshubai/pipeshub-ai:latest)
#   BENCH_TIERS   "label:cpus:memGB ..." (default "T-small:2:4 T-laptop:4:8 T-server:16:32")
#   BENCH_OUT     results directory (default ./bench-results/<date>)
#   BENCH_CORPUS  corpus directory (default ./bench-corpus; generated in the image if missing)
#   BENCH_REPEATS runs per tier and mix (default 3; the median is reported)
#   BENCH_MIXES   default "cpu-heavy io-heavy laptop-typical"
#   BENCH_GOLDEN  golden file to check the first run of each cell against
#   BENCH_MOUNT_APP  1 (default) runs the checkout's app code in the image; 0 runs the image's
#   BENCH_BUSY_RETRIES / BENCH_BUSY_WAIT  waits for a quiet host (default 10 x 60 s per run)
#   BENCH_PDF_PARSER  pdfplumber (default) or docling
#
# The container runs `python` directly (not docker-entrypoint.sh, which starts the services);
# the image's ENV (OMP_NUM_THREADS, HF_HOME, baked models) still applies.
# The host needs only docker, bash and python3 (standard library) for the final summary.
# T-server needs a host with at least 16 CPUs and about 36 GB free; a tier that does not fit
# is skipped with a message. T-big (64 / 128 GB) is not in the default list.
# Exit status is non-zero if any run failed, hit the busy-host limit, or lost its cgroup counters.
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
busy_retries=${BENCH_BUSY_RETRIES:-10}
busy_wait=${BENCH_BUSY_WAIT:-60}
pdf_parser=${BENCH_PDF_PARSER:-pdfplumber}
# Overridable so the dry run can be checked the same way on any machine.
host_cpus=${BENCH_HOST_CPUS:-$(nproc)}
host_mem_gb=${BENCH_HOST_MEM_GB:-$(awk '/MemTotal/{printf "%d", $2/1024/1024}' /proc/meminfo)}
uid_gid="$(id -u):$(id -g)"

run() { if ((dry)); then printf '%q ' "$@"; echo; else "$@"; fi; }

mount_app=()
if [[ ${BENCH_MOUNT_APP:-1} == 1 ]]; then
  mount_app=(-v "$python_root/app:/app/python/app:ro")
fi
code_mounts=(-v "$python_root/tests/perf/parse_bench:/bench/tests/perf/parse_bench:ro" "${mount_app[@]}")

# One container: python runs as root (the image's model cache is root's), then the files it
# wrote are handed back to the invoking user.
container() { # name, docker limit flags..., -- python args...
  local name=$1; shift
  local flags=()
  while [[ $1 != -- ]]; do flags+=("$1"); shift; done
  shift
  run docker run --rm --name "$name" --cgroupns=private "${flags[@]}" \
    --user 0 --entrypoint sh \
    -e PYTHONPATH=/app/python:/bench -e HF_HUB_OFFLINE=1 -e HOST_OWNER="$uid_gid" \
    "${code_mounts[@]}" "${extra_mounts[@]}" "$image" \
    -c 'python "$@"; rc=$?; chown -R "$HOST_OWNER" /out /corpus-out 2>/dev/null; exit $rc' sh "$@"
}

extra_mounts=()
if [[ ! -f $corpus/manifest.json ]]; then
  echo "generating the standard corpus in $corpus (inside $image)"
  run mkdir -p "$corpus"
  extra_mounts=(-v "$corpus:/corpus-out")
  container parsebench-corpus -- -m tests.perf.parse_bench.corpus --out /corpus-out
fi
run mkdir -p "$out"

golden_args=()
golden_mount=()
if [[ -n ${BENCH_GOLDEN:-} ]]; then
  golden_mount=(-v "$(realpath "$BENCH_GOLDEN"):/golden.json:ro")
  golden_args=(--golden-check /golden.json)
fi

failed=0
limits=()
for tier in $tiers; do
  IFS=: read -r label cpus mem <<<"$tier"
  limits+=(--tier "$label=$mem")
  if ((cpus > host_cpus || mem + 4 > host_mem_gb)); then
    echo "skip $label: needs $cpus CPUs and $((mem + 4)) GB, this host has $host_cpus and $host_mem_gb"
    continue
  fi
  run mkdir -p "$out/$label"
  for mix in $mixes; do
    for ((r = 1; r <= repeats; r++)); do
      result="$out/$label/$label-$mix-r$r.json"
      # Only a finished run with unchanged output is skipped; an aborted one or a golden diff runs again.
      if [[ -s $result ]] && PYTHONPATH="$python_root" python3 -m tests.perf.parse_bench.matrix done "$result"; then
        echo "skip $result (exists)"; continue
      fi
      echo "== $label $mix run $r: --cpus $cpus --memory ${mem}g"
      extra_mounts=("${golden_mount[@]}" -v "$corpus:/corpus:ro" -v "$out/$label:/out")
      check=()
      ((r == 1)) && check=("${golden_args[@]}")
      rc=0
      for ((attempt = 0; attempt <= busy_retries; attempt++)); do
        # Swap off (memory-swap = memory) so memory.peak is the working set.
        container "parsebench-$label" --cpus "$cpus" --memory "${mem}g" --memory-swap "${mem}g" -- \
          -m tests.perf.parse_bench.driver --corpus /corpus --mix "$mix" --label "$label" \
          --pdf-parser "$pdf_parser" --cgroup /sys/fs/cgroup --require-cgroup \
          --out "/out/$label-$mix-r$r.json" "${check[@]}" && rc=0 || rc=$?
        ((rc == 3)) || break  # 3: host busy, driver did not start
        echo "host busy; waiting ${busy_wait}s (attempt $((attempt + 1))/$((busy_retries + 1)))"
        ((dry)) && break
        sleep "$busy_wait"
      done
      if ((rc != 0)); then
        echo "RUN FAILED rc=$rc: $label $mix run $r (3 = host stayed busy, 4 = stopped at 85 % memory, 5 = golden diff, 6 = cgroup counters unreadable)" >&2
        failed=1
      fi
    done
  done
done

echo "== summary"
args=()
for tier in $tiers; do
  label=${tier%%:*}
  [[ -d $out/$label ]] && args+=(--out-dir "$out/$label")
done
((dry)) && { echo "(dry run: nothing executed)"; exit 0; }
((${#args[@]})) || { echo "no tier ran" >&2; exit 1; }
PYTHONPATH="$python_root" python3 -m tests.perf.parse_bench.matrix summarize "${args[@]}" "${limits[@]}" | tee "$out/summary.md"
echo "results: $out"
exit $failed
