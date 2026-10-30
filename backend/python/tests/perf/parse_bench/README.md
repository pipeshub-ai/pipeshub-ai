# Parse benchmark

Measures how the parsing path uses a machine: files/s, event-loop lag, memory,
CPU, threads and workers. It drives what `app/api/routes/parsing.py` drives,
without HTTP: the real `ResourceGovernor` and its gates admit each file; the
real providers parse it; the parse pool, PdfPlumber+OpenCV (the default PDF
path), local Docling for DOCX, and the VLM OCR strategy with only the model call
stubbed (pages are still rendered, a fixed delay per page stands in for the model;
each OCR page goes through `DoclingMarkdownParser`, as `parsing_main.py:151`).

Two arms, labelled in every result and table:

- **PdfPlumber arm (not yet the shipped default)**: `ENABLE_PDFPLUMBER_PROCESSOR=true`, the
  primary arm. Compose files and the code default are still `false` today; this is the
  path the programme optimises and the one whose cost lands in the parsing container.
- **Docling arm (upper bound, local Docling)**: `--pdf-parser docling`. It runs Docling
  in-process for PDFs. Today's `false` setting instead sends PDFs to the remote Docling
  service, so this arm is neither deployment; read it as an upper bound on Docling cost,
  not as a regression baseline for existing installs.

What it does not do (caveats):

- HTTP, auth, the Kafka/Redis consumer, LLM table summaries (`skip_table_enrichment`), the
  indexing stages after parsing.
- The route's `parse_admission_cap`, per-org share check, password check for office files and
  `scope_from_claims` are not in the driver. The governor is built with `worker_count=1`,
  no `MAX_CONCURRENT_PARSING`, and no local-embedding CPU reservation, where a deployed
  parsing service may set all three. The loop's thread pool headroom is the service's (4).
- `OMP_NUM_THREADS` is unset on the host and 2 in the image. Measured on this host at 4 CPUs
  (cpu-heavy): 0.190 vs 0.193 files/s, no effect, but check it if a tier run disagrees.
- Loop-lag p99 on a run of a few hundred samples is the third-worst sample. Six identical
  io-heavy runs gave 112-295 ms. Use the median and the min-max over repeats (`matrix.py`
  prints both) and do not read one run's p99. Throughput repeats to about 1-5 %; lag does not.

| File | Role |
|---|---|
| `corpus.py` | seeded corpus: digital, scanned (up to the OCR page limit), table-heavy, figure-heavy, damaged and over-100 MB PDFs, DOCX, and small md/json/txt |
| `driver.py` | one run of one mix; writes JSON and a table |
| `matrix.py` | several mixes x repeats in fresh processes, then medians and the targets table |
| `golden.py` | canonical hash of every parsed file, and the diff |
| `run_tiers.sh` | the cgroup tier runs in Docker |
| `report.py` | statistics, cgroup counters, tables (stdlib only) |

Run everything from `backend/python`. The corpus generator needs `reportlab`,
`python-docx`, `Pillow` and `numpy`, which the app already depends on.

## 1. Corpus

```bash
python -m tests.perf.parse_bench.corpus --out /data/bench-corpus            # standard: about 150 MiB, 64 files
python -m tests.perf.parse_bench.corpus --out /tmp/c --profile smoke        # 10 files, 0.5 MiB
```

Parameters: `--seed`, `--scale` (multiplies file counts), `--large-mb` (pixel bytes of the
over-100 MB PDF, default 105, which gives a 135 MiB file because the PDF is about 1.3x the
pixels; 0 skips it), `--boundary-pages` (the scanned file at the OCR limit;
`VLM_OCR_MAX_PAGES` is 500), and `--max-mb`. `--max-mb` is a true upper bound: a conservative
estimate refuses early, and every file is checked against the budget by its real size
before it is written. A corpus that would cross it is refused (exit 2, files removed). The same seed and parameters
give the same bytes; `manifest.json` has a `corpus_id` over them.

## 2. The tier runs (owner, needs Docker)

```bash
cd backend/python
BENCH_IMAGE=pipeshubai/pipeshub-ai:latest \
BENCH_CORPUS=/data/bench-corpus \
BENCH_OUT=/data/bench-results/$(date +%F) \
BENCH_GOLDEN=/path/to/golden-standard-seed1.json \
  tests/perf/parse_bench/run_tiers.sh
```

For each tier, mix and repeat it starts a fresh container with
`--cpus C --memory Mg --memory-swap Mg --cgroupns=private` (swap off, so `memory.peak` is the
working set; a private cgroup namespace so `/sys/fs/cgroup` is the container's own). One
container per run is what makes `memory.peak` the peak of that run: the file is the container's
high-water mark since it started, and resetting it needs a writable cgroup mount that Docker
does not give by default. The peak includes page cache of the corpus and model files, as the OOM
killer's accounting does. The driver is started with `--require-cgroup`, which exits 6 if
`memory.peak`, `cpu.stat` or `memory.events` cannot be read, so a result is never silently
labelled emulated. The script prints and saves (`$BENCH_OUT/summary.md`) the median run per
cell, the min-max spread, and the targets table, and exits non-zero if any run failed. Default tiers: `T-small:2:4 T-laptop:4:8 T-server:16:32`
(override `BENCH_TIERS="label:cpus:memGB ..."`). `--dry-run` prints the
commands.

- The container runs the checkout's `app/` (mounted read-only over the image's)
  and this directory, so the image only supplies dependencies and models. Set
  `BENCH_MOUNT_APP=0` to measure the image's own code.
- **T-server (16 CPUs, 32 GB) needs a host with at least 16 CPUs and about 36
  GB free.** The script skips a tier that does not fit and says so. A 8-CPU /
  32 GB host runs T-small and T-laptop only. T-big (64 / 128 GB) needs a real
  host and is not in the default list.
- The corpus, if missing, is generated inside the image (the host needs only docker, bash and
  python3 for the summary). The container runs `python` through `sh`, not
  `docker-entrypoint.sh` (which starts the services); the image ENV still applies. Result files
  are chowned back to the invoking user.
- Run on a quiet host: the driver refuses to start when host CPU or memory is
  at 85 % or more (exit 3). The script waits `BENCH_BUSY_WAIT` (60 s) up to
  `BENCH_BUSY_RETRIES` (10) times per run, then reports `RUN FAILED` and exits non-zero. Record
  `loadavg_1m` from each result's `host` block. The plan asks for under 30 %
  load before a tier run.
- `cgroup.memory_peak_scope` is "container lifetime" in tier runs, which is the run because
  the container is fresh. (`matrix.py` run inside one long-lived container would also try a
  per-fd reset, which works only where `/sys/fs/cgroup` is writable.)
- The first run of each cell is checked against `BENCH_GOLDEN`.

## 3. Without Docker (emulated tiers)

```bash
python -m tests.perf.parse_bench.driver --corpus C --mix cpu-heavy --cpus 2 --mem-limit-gb 4 --out run.json
python -m tests.perf.parse_bench.matrix run --corpus C --out-dir out/emu-2 --label emu-2cpu-4gb --cpus 2 --mem-limit-gb 4
python -m tests.perf.parse_bench.matrix summarize --out-dir out/emu-2 --tier emu-2cpu-4gb=4
```

`--cpus N` pins the process (and its workers) to N CPUs, which
`effective_cpu_count()` and the governor read; `--mem-limit-gb G` tells the
governor that limit with a working set taken from the process tree, and stops
the run at 85 % of it. **This is not a cgroup run**: nothing enforces the
limits in the kernel, there is no CFS throttling, and `cgroup` is `null` in the
result. Use it to compare code on one host, not to fill the targets table.

## 4. Golden diff ("output unchanged")

```bash
# on the reference checkout (main): write
python -m tests.perf.parse_bench.driver --corpus C --mix all --golden-write golden.json
# on the branch under test: check; exit code 5 and a list of files if anything differs
python -m tests.perf.parse_bench.driver --corpus C --mix all --golden-check golden.json
```

Each file's parsed container is reduced to a canonical form (uuids renamed by
order of first appearance) and hashed; errors (the damaged PDFs) are compared by
code. A run of a single mix checks only the files it parsed. The result must
not depend on CPU count: the same golden file holds at 1, 2 and 4 CPUs.

## What a result holds

`config` (mix, CPUs, limits, env), `host` (load average), `status`, `files`,
`governor` (ceilings and limits at the end), `metrics` (wall time, files/s,
latency p50/p95/p99 per type including the wait for a gate slot, loop lag
p50/p99/max, CPU seconds from `getrusage`, peak RSS of the parent and of the
whole tree, PSS, peak threads, parse workers, ONNX threads x workers, and for
each parse worker its first and peak RSS and thread count), `cgroup`, `golden`.
Per-worker peak RSS is the layout models' cost (G-11); run with
`PDF_TABLE_MODEL=off` for the difference.
