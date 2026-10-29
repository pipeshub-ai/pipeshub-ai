# Parse benchmark

Measures how the parsing path uses a machine: files/s, event-loop lag, memory,
CPU, threads and workers. It drives what `app/api/routes/parsing.py` drives,
without HTTP: the real `ResourceGovernor` and its gates admit each file; the
real providers parse it; the parse pool, PdfPlumber+OpenCV (the default PDF
path), local Docling for DOCX (and for PDFs with `--pdf-parser docling`, the
`ENABLE_PDFPLUMBER_PROCESSOR=false` setting), and the VLM OCR strategy with only
the model call stubbed (pages are still rendered, a fixed delay per page stands
in for the model).

What it does not do: HTTP, auth, the Kafka/Redis consumer, LLM table
summaries (`skip_table_enrichment`), or the indexing stages after parsing.

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

Parameters: `--seed`, `--scale` (multiplies file counts), `--large-mb` (the
over-100 MB PDF, default 105, 0 skips it), `--boundary-pages` (the scanned file
at the OCR limit; `VLM_OCR_MAX_PAGES` is 500), and `--max-mb`, which refuses to
write a corpus whose estimate is over the budget. The same seed and parameters
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

For each tier it runs one container with `--cpus C --memory Mg --memory-swap Mg`
(swap off, so `memory.peak` is the working set), runs the three mixes
(`cpu-heavy`, `io-heavy`, `laptop-typical`) three times each in fresh
processes, and prints and saves (`$BENCH_OUT/summary.md`) the median run per
cell and the targets table. Default tiers: `T-small:2:4 T-laptop:4:8 T-server:16:32`
(override `BENCH_TIERS="label:cpus:memGB ..."`). `--dry-run` prints the
commands.

- The container runs the checkout's `app/` (mounted read-only over the image's)
  and this directory, so the image only supplies dependencies and models. Set
  `BENCH_MOUNT_APP=0` to measure the image's own code.
- **T-server (16 CPUs, 32 GB) needs a host with at least 16 CPUs and about 36
  GB free.** The script skips a tier that does not fit and says so. A 8-CPU /
  32 GB host runs T-small and T-laptop only. T-big (64 / 128 GB) needs a real
  host and is not in the default list.
- Run on a quiet host: the driver refuses to start when host CPU or memory is
  at 85 % or more (exit 3, `matrix.py` waits and retries), and record
  `loadavg_1m` from each result's `host` block. The plan asks for under 30 %
  load before a tier run.
- `cgroup.memory_peak_scope` says whether `memory.peak` was reset for the run
  (kernel 5.19+) or is the container's lifetime peak.
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
