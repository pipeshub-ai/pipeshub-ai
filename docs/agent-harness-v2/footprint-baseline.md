# Footprint baseline (P0.9)

Memory of the all-in-one Compose stack, `eval` profile (slim image, Neo4j, Redis Streams + Redis KV,
no sandbox, no Slack bot), idle after health, with and without the durable-harness processes.
Measured by `deployment/docker-compose/tests/footprint.sh`; budgets and the CI check (S1) are in
`SLO.md` rows 15–16.

## Machine

| | |
|---|---|
| Host | Apple M4, 10 cores, 24 GB, macOS (arm64) |
| Docker | Docker Desktop 28.0.4, linux/aarch64 VM, 14,967 MiB RAM (16 GB setting), 10 CPUs |
| App memory limit | installer-derived (VM RAM − 4 GB); the numbers are usage, not limits |
| Date | 2026-09-26 |

## Method

1. `footprint.sh` copies this checkout's `docker-compose.yml` and `install.sh` to a temp dir and runs
   `PIPESHUB_DEPLOY_TYPE=eval install.sh --yes` under a unique project name (`pipeshub-footprint-eval-<pid>`)
   and port, so no other stack on the machine is touched.
2. Health (`/api/v1/health/services`: query, connector, indexing, docling), then 120 s idle.
3. `footprint.py measure`: 3 samples 10 s apart, peak kept. Containers from `docker stats`; processes
   from `/proc/<pid>/{status,smaps_rollup}` inside `pipeshub-ai`, each child charged to its nearest
   named ancestor (PSS splits pages shared with forked workers; RSS double-counts them).
4. Sets `AGENT_HARNESS_RELAY_ENABLED=true` and `AGENT_HARNESS_WORKER_ENABLED=true` (the
   `docker-compose.agent-harness.yml` settings), recreates only the app container, waits for health,
   idles 120 s and measures again. Then `install.sh --uninstall` and `down -v`.

**Image.** No published image contains `app/harness_main.py` yet (`:slim` and the local `:local` build
both predate it), so the measured image was the published `:slim` with this branch's Python code
and supervisor layered on top:

```dockerfile
FROM pipeshubai/pipeshub-ai:slim
COPY app/ /app/python/app/                    # backend/python/app from this branch
COPY harness_core/ /app/python/harness_core/
COPY process_monitor.sh /app/process_monitor.sh  # the heredoc from this branch's Dockerfile
```

Node in that image is `main`'s, so the relay's own cost inside `node-api` is not in these numbers
(it is a 1 s Mongo poll in the Node process). Run with
`PIPESHUB_VERSION=<tag> FOOTPRINT_NO_PULL=1 bash deployment/docker-compose/tests/footprint.sh`.

## Results

Containers (`docker stats` memory usage, MiB, peak of 3 samples)

| Container | eval, harness off | eval, harness on |
| --- | ---: | ---: |
| `mongodb` (single-node RS) | 151 | 129 |
| `neo4j` | 1,415 | 418 ¹ |
| `pipeshub-ai` | 3,577 | 4,199 |
| `qdrant` | 103 | 55 |
| `redis` | 14 | 13 |
| **total** | **5,260** | **4,814** ¹ |

Processes inside `pipeshub-ai` (PSS / RSS, MiB)

| Process | eval, harness off | eval, harness on |
| --- | ---: | ---: |
| `query` | 829 / 942 | 814 / 1,004 |
| `connectors` | 866 / 979 | 851 / 1,040 |
| `indexing` | 779 / 892 | 763 / 954 |
| `docling` | 600 / 707 | 587 / 770 |
| `embedding` (model not loaded until first use) | 430 / 510 | 413 / 565 |
| **`harness-worker`** | — | **667 / 852** |
| `node-api` | 163 / 165 | 159 / 180 |
| supervisor + other | 8 / 14 | 6 / 15 |

¹ Neo4j's reading fell by ~1 GB between the two measurements of the same, unchanged container
(only the app container was recreated); the cause was not investigated, and it is not a harness effect. Two
earlier harness-off runs on the published `:slim` image read 1,446 and 1,411 MiB for Neo4j and
5,451 / 5,323 MiB in total. The fair harness-on total is the harness-off total plus the app delta:
**5,260 + 622 ≈ 5,880 MiB**.

## Against the budgets

| Budget | Source | Measured | Verdict |
|---|---|---|---|
| `eval` total ≤ 7.5 GB (7,680 MiB) | the design's `desktop-eval` budget | 5,260 (off), ≈ 5,880 (on) | pass, 1.8–2.4 GB headroom |
| `eval` app container ≤ 4.5 GB (4,608 MiB) | the design's `desktop-eval` budget | 3,577 (off), 4,199 (on) | pass; the harness takes 60% of the remaining app headroom |
| Idle harness worker ≤ 350 MB | TEST-STRATEGY §8.1 | 667 PSS / 852 RSS | **fail** |
| Idle harness worker ≤ 350 MB, re-measured (WP-88, 2026-11-06) | TEST-STRATEGY §8.1 | 330 RSS (292 anonymous) on the host, after warm-up and 2 min idle | pass on the host; the image's `footprint.sh` run is not taken |
| Idle harness worker ≤ 350 MB, in the image (R25-OPS, 2026-11-11) | TEST-STRATEGY §8.1 | 723 PSS (children with their parent), `slim`, harness on, 120 s settle | **fail** (R25OPS-FU-1) |
| Idle sandbox broker ≤ 100 MB (R25-OPS, 2026-11-11) | TEST-STRATEGY §8.1 | 218 MiB (`docker stats`), `slim` | **fail** (R25OPS-FU-1) |
| Idle harness worker ≤ 350 MB, in the image (R26-FU, 2026-11-11) | TEST-STRATEGY §8.1 | 625 RSS (the worker is non-dumpable: no PSS), `slim`, harness on, 120 s settle | **fail**: the row predates C-17's warm-up, see "R26-FU re-measure" |
| Idle sandbox broker ≤ 100 MB (R26-FU, 2026-11-11) | TEST-STRATEGY §8.1 | 100 MiB (`docker stats`; 105 MB decimal), process 127 RSS / 95 anonymous | at the line |

## Findings

- **The worker costs ~0.6–0.85 GB idle**, confirming the design's estimate of 0.4–0.9 GB: it builds a
  full `QueryAppContainer`, so it is a second query-sized Python process. That is twice the laptop
  budget. Either the `desktop-eval` plan holds (RunDriver inline in the query process, no
  `harness_main`) or P1 slims the worker container (lazy retrieval/toolset wiring).
  Until then the harness must stay off in `eval`, which P0.6 already made the default.
- Idle headroom to the 7.5 GB total is ~1.8 GB with the harness off, before any document is indexed
  or the embedding model is loaded; these are idle numbers, not a sizing under load.
- Every Python service sits at 0.4–0.9 GB PSS idle; five of them make up ~3.5 GB of the app. A single
  run slot's working set is not in these numbers (P1 load test).

## WP-88 re-measure (2026-11-06)

The worker no longer builds a `QueryAppContainer`: `HarnessServices` (`app/harness/assembly/services.py`) wires the
logger, KV and configuration at start and every other service on the first run that needs it. Measured on the
32-core x86-64 Linux workstation, not in the image: the fault rig's worker (`integration-tests/harness/fault/worker.py`,
`app.harness_main.run_worker` with a scripted assembler; the same imports, warm-up and frozen startup heap) after
start and 120 s idle, three samples 40 s apart, peak: **330 MiB RSS** (292 anonymous, 38 file-backed; high-water
mark 330). The worker marks itself non-dumpable, so its PSS (`smaps_rollup`) cannot be read by another process;
RSS bounds PSS from above. The rig's Node (the fault-stack server under ts-node, not the app's compiled build):
247 MiB RSS, 209 MiB PSS. Left: `footprint.sh` with the harness on, in the image (the sandbox broker's ≤ 100 MB
too).

## R25-OPS image measure (2026-11-11)

`footprint.sh` on the shipped image built from this tree (`pxh-pipeshub-ai:r25ops`, `docker build` of the
repository's `Dockerfile` at `b54544ef4f` with R25-OPS), `PIPESHUB_DEPLOY_TYPE=slim` (the profile with the sandbox
broker; `eval` has none), `FOOTPRINT_NO_PULL=1`, on a 32-core x86-64 Linux workstation through the Docker lane: load
average 9.6 at the start and 9.3 at the end (1-minute), other lanes running, so not a quiet host. Peak of three
samples after 120 s idle.

| Container (MiB) | slim, harness off | slim, harness on |
| --- | ---: | ---: |
| `egress-proxy` | 238 | 238 |
| `mongodb` | 184 | 192 |
| `neo4j` | 1,485 | 1,489 |
| `pipeshub-ai` | 3,379 | 4,327 |
| `qdrant` | 169 | 169 |
| `redis` | 6 | 6 |
| `sandbox-broker` | 218 | 218 |
| **total** | **5,680** | **6,640** |

| Process in `pipeshub-ai` (PSS MiB) | harness off | harness on |
| --- | ---: | ---: |
| `harness-worker` | — | **723** |
| `model-gateway` | — | 350 |
| `query` | 852 | 852 |
| `connectors` | 833 | 832 |
| `indexing` | 762 | 762 |
| `docling` | 588 | 588 |
| `embedding` (the `latest` image's baked model) | 1,751 | 1,752 |
| `node-api` | 312 | 316 |
| `slack-bot` | 106 | 106 |

The idle worker is 723 MiB in the image against WP-88's 330 MiB RSS for the fault rig's worker on the host, and
the sandbox broker 218 MiB against its 100 MB row. Importing `app.harness_main` alone in the image is 276 MiB
peak RSS (numpy and pandas loaded, no torch), so the rest comes after start: what the real assembler, the engine
plan (engine runs were off here) or a child process add is not yet known. R25OPS-FU-1 finds it.

## R26-FU re-measure (2026-11-11)

Same command as R25-OPS (`footprint.sh`, `PIPESHUB_DEPLOY_TYPE=slim`, `FOOTPRINT_NO_PULL=1`, peak of three samples
after 120 s idle) on `pxh-pipeshub-ai:r26fu`: the R25-OPS image with this branch's Python code and the Dockerfile's
new bytecode step, so the two runs differ only in R26-FU's changes. Both measured on a 32-core x86-64 Linux
workstation through the Docker lane, other lanes running: before (R25-OPS image) load 8.8 at the probe, after load 18.4 to
19.9 (not quiet; idle memory does not depend on load, start time does).

| Container (MiB), harness on | before | after |
| --- | ---: | ---: |
| `egress-proxy` | 239 | 170 |
| `pipeshub-ai` | 4,324 | 3,990 |
| `sandbox-broker` | 219 | **100** |
| **total** (with `mongodb`, `neo4j`, `qdrant`, `redis`) | **6,653** | **6,181** |

| Process in `pipeshub-ai` (RSS MiB), harness on | before | after |
| --- | ---: | ---: |
| `harness-worker` | 723 | **625** |
| `model-gateway` | 350 | 270 |
| `query` | 852 | 784 |
| `connectors` | 832 | 789 |
| `indexing` | 762 | 716 |
| `docling` | 588 | 571 |

**What was held, and the fixes.** Found with an import profiler run in the image (RSS growth per module, nested
imports subtracted) and, in a running stack, a line-by-line RSS trace of `run_worker` and `tracemalloc` on a second
broker started beside the real one.

1. *No bytecode in the image.* The app's code is root-owned and the services run as uid 1000, so no process could
   write `__pycache__`, and the base image deletes site-packages' bytecode. Every service compiled every module it
   imports on every start and kept the compiler's peak in its heap: the 54,000-line generated API clients the
   toolset discovery imports (Outlook 45 MiB, Jira 30, OneDrive 13). The Dockerfile now compiles `app`,
   `harness_core` and `harness_engines` (adds ~100 MB to the image); every Python service is 17 to 68 MiB smaller
   and the worker's import and warm-up take 7.4 s instead of 12.2 s. Compiling site-packages too was measured and
   not taken: +582 MB of image for 3 MiB less in the worker.
2. *Eager package re-exports.* `harness_core.gateway`, `.journal`, `.kernel.durable`, `.effects`, `.interactions`,
   `.sandbox.egress`, `harness_engines.acp`, `app.harness.journal_node` and `app.agent_loop_lib.sandbox.coding`
   imported every submodule to re-export its names, so importing one submodule loaded the package. They resolve
   names on first read now (`harness_core/lazy_exports.py`): the broker's module graph is 891 modules, was 1,415
   (no FastAPI, journal client, durable kernel or tool gateway endpoint).
3. *`app.edition_services` re-exported `Neo4jProvider` and the connectors' token services eagerly.* The broker's
   image check asks that module only for a harness hook, and loaded the Neo4j driver with pandas and numpy (which
   the driver imports when installed) at start: the broker's 218 MiB was its ~85 MiB of code plus ~110 MiB of that.
   They resolve on first read now. The worker's module import fell from 270 to 190 MiB; its warm-up imports the
   provider `DATA_STORE` names (`graph_store_modules`), so on a Neo4j deployment the driver is still paid before the
   first claim (C-17), never on a run's loop, and on ArangoDB or PostgreSQL not at all.
4. *Startup garbage kept by the allocator.* `freeze_startup_heap` gives the freed heap back (`malloc_trim`, ~10 MiB).

**Why the worker is still over its row.** The running worker adds nothing at idle: the line trace in a running
stack (taken before fix 3, which moves ~80 MiB from the import to the warm-up) read 268 MiB after import, +27 MiB for the configuration, envelope key and Redis live bus, +66 MiB when the
tokenizer preload finished (o200k_base 43 MiB and cl100k_base 21 MiB of Rust rank tables, R14-LOOP), +274 MiB for
C-17's warm-up (the agent, retrieval and chat stacks, ~234 MiB, and the 32 toolsets' discovery, ~60 MiB), then
+1 MiB over two idle minutes; no child process, no pool. WP-88's 330 MiB was the fault rig's worker, whose
scripted assembler skips the warm-up: import plus tokenizers. The row's 350 MB therefore measures a worker that has
not imported what its first run imports; C-17 moved that cost to the start on purpose (a fresh worker's first run,
often a recovery, spent 8.9 s importing and missed lease renewals). A warmed worker below ~550 MiB needs the agent
stack itself to shrink, chiefly the toolset discovery importing every toolset's client SDK and the chat route
module (`app.api.routes.agent`, 37 MiB, with the attachment parsers OpenCV and the entity resolver) that the
assembly takes helpers from. The row is not changed here: R26FU-FU-1 asks for the decision.

## Not measured here

| What | Why | Where it gets measured |
|---|---|---|
| 8 GB Docker Desktop VM | the machine's VM is 16 GB; changing it restarts Docker Desktop and every container on it. Idle usage does not depend on VM size; the app's cgroup limit (and the governor's pool sizing under load) does | release checklist on Apple silicon (TEST-STRATEGY §8.1) |
| x86 | not measured on x86 | S1: the `eval` job of `published-hub-smoke.yml` runs `footprint.py check` on the x64 runner (harness off) |
| `slim` / `full` profiles | same processes as `eval` plus the Slack bot (and Kafka + Zookeeper for full); not budgeted | `PIPESHUB_DEPLOY_TYPE=slim|full bash footprint.sh` |
| Relay cost in Node, sandbox broker | relay not in the published Node build; broker lands in P5 | rerun once an image with both is published |
| Under load | idle only | P1 `bench_harness.py` with `footprint.py measure` during the run |
