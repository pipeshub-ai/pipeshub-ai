# Query-service load testing

Measures what the query service actually does under load: **throughput, turn
latency, CPU, memory and backend call latency**, from one command.

Everything here is diagnostic tooling. Nothing under `backend/` is modified —
the probes are copied into the running container and removed again by
`./instrument.sh off`.

---

## Quick start

On the machine running PipesHub:

```bash
cd loadtest
cp .env.example .env                  # then put your TOKEN in it
pip3 install --user py-spy            # optional, for the CPU flame graph

./instrument.sh on                    # docker only; adds phase + backend timing
./perftest.sh baseline 8 300          # label, users, seconds
```

`TOKEN` is a bearer JWT — browser devtools, any API request, the
`Authorization` header. Everything else in `.env` has a working default.

That writes `results/baseline/`, printing the report to the terminal too:

| file | what it is |
|---|---|
| `report.txt` | the printed report |
| `cpu.svg` / `cpu.raw.gz` | flame graph, and the raw profile for re-analysis |
| `memory.svg` / `memory.csv` | RSS over the run |
| `requests.csv` | every request: HTTP code, duration, curl exit |
| `summary.json` | machine-readable, consumed by `aggregate.py` |

Comparing many configurations:

```bash
./aggregate.py results        # median + min-max per config, writes matrix.csv
```

To compare a change, run it again with a different label and diff the reports:

```bash
./perftest.sh after 8 300
diff results/baseline/report.txt results/after/report.txt
```

### Arguments

```
./perftest.sh <label> [users] [seconds] [workers]
```

`workers` is optional; supplying it restarts the container with that many
uvicorn workers first. Omit it to test whatever is already running.

```bash
./perftest.sh w1_u8   8  300 1     # 1 worker,  8 users
./perftest.sh w4_u32 32  300 4     # 4 workers, 32 users
```

---

## Docker or native

The deployment is detected automatically. A **running** container named
`pipeshub-ai` wins; otherwise a query service found on port 8000 is used. A
container that exists but is stopped does not count — that is the machine where
PipesHub moved to host processes and the container is a leftover. Force it with
`PIPESHUB_MODE=docker|native` in `.env`.

Native runs measure less, because the probes that produce phase and backend
timings are installed *into a container* — doing that natively would mean
editing your working tree:

| section | docker | native |
|---|---|---|
| throughput, turn latency, backend calls | yes | needs `PIPESHUB_QUERY_LOG` + instrumentation |
| CPU flame graph | yes | yes (py-spy attaches to the host pid) |
| memory | yes | yes |
| load generation | yes | yes |

For native runs set `PIPESHUB_QUERY_LOG` to wherever the query service's
console output goes — `docker logs` is what supplies it otherwise. On Windows
the memory figure is private bytes rather than RSS: Windows trims the working
set of an idle process, so a working-set graph plots OS behaviour instead of
the application.

### Requirements

- **Bash** — Linux, macOS, WSL, or Git Bash. The entry points use arrays and
  `mapfile`, so run them with `bash`, not `sh`.
- **Python 3.8+** as `python3` or `python`.
- **Docker** (docker mode only). Access is probed with and without `sudo`, so
  Docker Desktop, rootless and stock Linux daemons all work.
- **py-spy** — optional; without it the CPU section is skipped and the rest
  still runs.

Every script checks its target up front and exits non-zero with the reason,
rather than reporting a number it could not have observed.

---

## What you get

```
==================== THROUGHPUT ====================
  completed turns : 88
  window          : 316s
  throughput      : 16.7 req/min  (0.278/s)
  steady-state    : 84 turns in the 300s load window  (16.8 req/min)

==================== REQUESTS (client-side) ====================
  requests        : 88
  succeeded (200) : 88
  failed          : 0  (0.0%)
  turn seconds    : p50=24.1 p95=34.9 max=41.2

==================== TURN LATENCY (per phase, ms) ====================
  turn total  mean=24313  p50=23575  p95=34280
  llm_stream       mean=17004   70.0%
  retrieval_wait   mean= 4820   19.8%
  agent_create     mean= 1204    5.0%
  ...

==================== CPU (top functions, real work only) ====================
  19057 samples of executing Python
  by subsystem:
     26.03%  record fetch + decode
     20.95%  graph driver
      5.27%  citation processing
  ...

==================== MEMORY (query service RSS, MB) ====================
  start=1019 MB  peak=1153 MB  end=1153 MB  growth=+134 MB
  ▁▁▃▆▆▆▇███████████

==================== BACKEND CALLS (caller-side) ====================
  backend         calls      p50   p95 worst      max
  neo4j           47030     16ms      1974ms   1974ms
  node_api        14938     92ms      2366ms   2366ms
```

**Reading it**

- *Throughput* is counted **server-side**, one per completed turn. Client-side
  counting drops turns that are still streaming when the run stops, which
  penalises exactly the slow configs you are comparing.
- *steady-state* counts only turns finished while load was still being offered.
  The line above it divides by elapsed time **including the drain** after load
  stops, so a config whose last turns take two minutes reports a lower rate for
  the same work. When the two disagree, the drain is doing the talking.
- *Requests* is the client's view. A run that mostly failed used to be
  indistinguishable from a slow one — both just showed low throughput. Above a
  5% error rate the run is marked `** INVALID` and `aggregate.py` drops it from
  the medians.
- *CPU by subsystem* counts a sample for every subsystem in its stack, so
  nested cost lands on whatever caused it. The by-function list underneath is
  the raw leaf view.
- *Backend latency* is measured **caller-side**, so it includes queueing. A
  backend whose own latency is flat while this number grows is not slow — the
  queue in front of it is.
- *`cpu.svg`* opens in a browser. Width is time; colour means nothing. Click to
  zoom, hover for exact percentages, and use the search box (top right) to
  total up a subsystem.

---

## Configuration

All settings live in `.env` (gitignored; copy `.env.example`). Real environment
variables override the file, so one-off runs need no edit:

```bash
PIPESHUB_MODE=docker ./perftest.sh baseline 8 300
```

| variable | default | what it does |
|---|---|---|
| `TOKEN` | — | bearer JWT; required unless `users` is 0 |
| `PIPESHUB_MODE` | `auto` | `auto` / `docker` / `native` |
| `PIPESHUB_HOST` | `http://localhost:3000` | API gateway the load hits — the Node backend, not the frontend |
| `PIPESHUB_CONTAINER` | `pipeshub-ai` | docker mode only |
| `PIPESHUB_QUERY_LOG` | — | native mode: where query console output goes |
| `PIPESHUB_QUERY_PORT` | `8000` | native mode: used to find the pid |
| `PIPESHUB_QUERY_PID` | — | native mode: skip the pid lookup |
| `PIPESHUB_QUERY_FILE` | `queries.txt` | the query set users rotate through |
| `PIPESHUB_QUERY` | — | force a single query instead of the file |
| `PIPESHUB_THINK_TIME` | `3` | seconds between a user's turns |

`perftest.sh` probes the token before starting and aborts if it is not
accepted — an expired token turns every request into a 401, which otherwise
looks exactly like a throughput collapse.

### Without py-spy

CPU profiling is skipped with a note; throughput, latency, memory and backend
sections still work. py-spy must run on the host (the container has no
`SYS_PTRACE`), which is why it attaches from outside.

### Load from a second machine

Running the load generator on the host under test costs it some CPU. For a
demo that is fine. For numbers you intend to publish, drive the load from
elsewhere and use this only to collect:

```bash
# on the host: collect for 300s while someone else drives load
./perftest.sh observed 0 300
```

`users=0` starts no load of its own.

---

## Files

| file | what it does |
|---|---|
| `perftest.sh` | the one command — runs load, collects everything, writes the report |
| `instrument.sh` | `on` / `off` / `status` for the in-container probes |
| `throughput.sh` | completed turns in a window (used by `perftest.sh`; usable alone) |
| `query_rss.sh` | query-service RSS in MB, one number, for sampling |
| `set_workers.sh` | change the uvicorn worker count (restarts the container) |
| `restart_query.sh` | restart only the query process, leaving the container up |
| `locustfile_play.py` | optional locust scenario, if you prefer locust's latency percentiles |
| `queries.txt` | the query set users draw from — **edit this for your corpus** |
| `aggregate.py` | rolls many runs' `summary.json` into one table + `matrix.csv` |
| `_common.sh` | sourced by the rest: finds docker (with/without sudo), finds Python, checks the container is up |
| `instr/` | the probes copied into the container, plus the report aggregators |

`results/` is gitignored — reports, profiles and CSVs stay local.

---

## Gotchas that have cost real time

- **A stopped container used to read as an idle service.** `query_rss.sh`
  returned `0 MB` and `throughput.sh` `0.0 req/min` when there was no container
  to talk to — indistinguishable from a real measurement of a quiet system.
  Every script now checks first and refuses to produce a number it cannot back.
  A native deployment is still measurable — CPU and memory work as they are, and
  load generation is unaffected; only the log-derived sections (throughput, turn
  latency, backend calls) need `PIPESHUB_QUERY_LOG` pointed at the query
  service's output.
- **`docker compose up -d` wipes the probes.** It recreates the container.
  `docker restart` keeps them. Re-run `./instrument.sh on` after a compose up.
- **Don't `docker cp` a source file from a branch newer than the running
  image.** Testing a change by copying files into the container only works if
  the rest of the codebase agrees with them. Copying a file whose module
  interface has moved on takes the service down at import time — the symptom is
  a health check that never passes, and `docker logs` showing
  `ImportError: cannot import name ... from ...`. Copy files built from the
  same commit the image was.
- **Measure on an idle system.** Background indexing inflates latency and CPU
  and silently corrupts a comparison. Check the record count is stable first.
- **Never change worker count mid-run** — `set_workers.sh` restarts the
  container.
- **One trial is ±10%.** Treat a single run as directional. For a claim worth
  publishing, run 3–4 trials per config and compare medians; CPU-profile
  percentages are far more stable than throughput.
- **One repeated query is not a workload.** Identical prompt prefixes hit the
  provider's prefix cache and retrieval stays warm, so a single fixed question
  overstates throughput substantially — and a question the corpus cannot answer
  returns a fast empty turn that inflates the rate further. Use `queries.txt`;
  reach for `PIPESHUB_QUERY` only to pin one query so both arms of an A/B carry
  the same bias, never to quote a capacity number.
- **Check the answers, not just the rate.** Turns returning 0 citations are
  measuring the not-found path, not retrieval. A run can look healthy on
  req/min while most of its answers are empty.
- **A growing corpus biases comparisons.** Load tests create records. If a
  baseline and its comparison are days apart, note the record counts.
