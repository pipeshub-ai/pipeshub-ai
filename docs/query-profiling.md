# Query service profiling

`loadtest/qprof.sh` puts the query service under chat load and reports requests
per minute, turn latency, CPU and memory per container, plus a flame graph. It
runs every combination of user counts (and optionally worker counts) several
times and summarises them in one table. It works on Docker deployments only.

## Setup (once)

On the machine running PipesHub:

```bash
cd loadtest
cp .env.example .env          # set PIPESHUB_USERS (or TOKEN); see below
pip3 install --user py-spy    # for the flame graph
```

`PIPESHUB_USERS=email:password,…` gives each simulated user its own account,
which is what you want. `./seed_users.py --count 8` creates test accounts and
prints that line. py-spy has to attach as root, so run as root or with
passwordless sudo, or pass `--flame off`.

## Run

```bash
./qprof.sh                                  # 8 users, 3 trials of 5 min
./qprof.sh --users 8,16,32 --sets 3         # find the throughput ceiling
./qprof.sh --users 16 --workers 1,4         # compare worker counts
```

| flag | default | meaning |
|---|---|---|
| `--users` | `8` | parallel users; comma list runs each |
| `--sets` | `3` | trials per combination |
| `--duration` | `300` | seconds of load per trial |
| `--workers` | current | uvicorn workers; comma list runs each, original count is restored at the end |
| `--flame` | `cpu` | `cpu`, `wall` (includes threads blocked in sync I/O) or `off` |
| `--label` | `qprof-<timestamp>` | results folder name |
| `--think` | `3` | seconds a user waits between turns |
| `--cooldown` | `30` | idle seconds between trials |
| `--queries` | `queries.txt` | query set, one per line |
| `--turn-timeout` | `900` | seconds before one turn is abandoned and counted as failed |
| `--no-instrument` | | skip the phase/backend timing probes |
| `--idle-cpu` | `80` | wait before each trial until the stack has used under this much CPU (% of one core, all containers) for a minute; `0` turns the wait off |
| `--idle-timeout` | `14400` | longest wait for idle, in seconds |
| `--no-warmup` | | skip the unmeasured pass over the query file that precedes the first trial after a restart |
| `--order` | `random` | `unique` asks every question once before any repeats (see below) |

Pin the model's reasoning effort with `PIPESHUB_REASONING_EFFORT` in `.env`. It
changes the cost of a turn more than most code changes do, and the model's
default can differ between deployments.

Before the first trial it installs the timing probes (`instrument.sh on`) and
checks that py-spy can attach. If either fails it stops rather than running
trials that would come back empty. The probes are left in place afterwards.

## Measuring without repeated questions

With the default `queries.txt`, ten questions rotate through every user. Every
cache in the serving path then hits almost every time, which overstates
throughput for real traffic. To measure what a new question costs, generate one
question per indexed record and deal them out without repeats:

```bash
./make_queries.py                           # one question per indexed record -> queries_unique.txt (Neo4j only)
./qprof.sh --queries queries_unique.txt --order unique --no-warmup --users 24
```

Trials with the same users and trial number ask the same questions in the same
order, so two arms of an A/B stay comparable. `--count N` writes a smaller sample;
keep N above the number of turns a trial runs, or questions start repeating. `report.txt` prints
`distinct questions: X of N` to confirm nothing repeated.

## Output

`results/<label>/summary.txt` holds the table you usually want, and
`scaling.svg` plots throughput, latency and CPU against users. Per combination
it shows req/min (median and min–max across trials), p50 turn time, error rate,
and CPU avg/max and memory avg/peak for every container and for the query
workers.

Each trial has its own folder, `results/<label>/w<workers>_u<users>_t<set>/`:

| file | what it is |
|---|---|
| `report.txt` | throughput, failed answers, per-phase latency, top CPU functions, resources and saturation, backend call latency, tool work per turn |
| `cpu.svg` | flame graph; open in a browser, click to zoom, search box top right |
| `cpu.speedscope.json` | same capture; drop on [speedscope.app](https://www.speedscope.app) for a timeline and per-thread view |
| `resources.svg` | timeline of in-flight turns, host CPU, and CPU and memory per container and per service |
| `memory.svg` | query service memory over the run |
| `requests.csv`, `resources.csv` | raw samples; `requests.csv` has each turn's start and end |
| `server.log.gz` | the service's log for the run, captured live so log rotation cannot lose it |

## Reading it

- **req/min** is counted on the server from completed turns. The *steady-state*
  figure in each report leaves out the drain after load stops. Use it when the two disagree.
- A turn whose answer is an error message (HTTP 200, e.g. "agent run failed")
  counts as failed, not as throughput. More than 5% failed requests or answers
  marks a trial `INVALID`, and it is left out of the medians.
- `SATURATION` in the report shows how often the busiest query process sat
  above 90% of one core (one event loop is the ceiling) and how often the whole
  host was above 90%. The first means more workers would help; the second means they would not.
- Memory is shown without page cache, the same figure `docker stats` gives.
- A single trial varies by about ±10%. Compare medians and keep `--sets` at 3 or more.
- Measure on an idle stack, with nothing indexing in the background.

More detail on the underlying scripts and their pitfalls is in [`loadtest/README.md`](../loadtest/README.md).
