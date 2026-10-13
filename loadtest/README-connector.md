# Connector-service load harness

Measures how fast the connector service turns a data source into graph records,
and what that costs the API, with **indexing switched off** so the numbers
describe the connector service alone.

Built to answer two questions:

1. **Throughput** — records/second the connector service can discover and write.
2. **Blocking** — does a running sync stall the HTTP API, and if so, in which
   function.

Set `LT_HOST` to the machine you run against; nothing here hardcodes one.

## Quick start

```bash
pip install -r loadtest/requirements.txt

# On a fresh stack: creates the org + admin, mints an OAuth client,
# and writes PIPESHUB_BASE_URL / CLIENT_ID / CLIENT_SECRET into loadtest/.env
python loadtest/run.py bootstrap

docker compose -f loadtest/compose/minio.yml up -d
python loadtest/run.py doctor --source minio     # verify everything is reachable

python loadtest/run.py run smoke                 # ~2 min, proves the wiring
python loadtest/run.py run fleet                 # the real measurement
```

If the stack already has an org, skip `bootstrap` and put an existing
`CLIENT_ID`/`CLIENT_SECRET` in `loadtest/.env` yourself — see `.env.example`.

Each run writes `loadtest/runs/<run-id>/` containing `report.html` (open it
directly, no server needed), `result.json`, and the raw JSONL behind both.

## A/B comparison

```bash
python loadtest/run.py run fleet --label before
#   ... change the code, rebuild, restart the stack ...
python loadtest/run.py run fleet --label after

python loadtest/run.py compare loadtest/runs/before loadtest/runs/after
```

The verdict is deliberately conservative. A difference only counts when it
exceeds the noise both runs demonstrated about themselves, so a 4% "win"
between two runs that each wobble 6% is reported `INCONCLUSIVE`, not as a
result. It reports `INVALID` outright when the two runs are not comparable —
different corpus, different source, a non-deterministic source, an iteration
that timed out, or records that leaked into indexing.

## What is actually measured

**Throughput** is `total records / time for the slowest connector to reach its
expected count`, read from `GET /api/v1/connectors/{id}/stats`, medianed across
`repeat` iterations after `warmup` discarded ones.

**API impact** comes from a constant-rate prober running for the whole sync,
issuing two request classes:

| probe | path | meaning |
|---|---|---|
| `through` | `/api/v1/connectors/` | traverses the connector service |
| `control` | `/api/v1/org/health` | served by the Node API alone (~2.6 ms) |

Do **not** use `/api/v1/health/services` as the control: it actively probes all
five services, measured 1.5 s, and made the baseline slower than the thing under
test while adding real load twice a second.

When `through` degrades and `control` does not, the stall is inside the
connector service's event loop rather than in Node, the proxy hop, or the host.
Alongside percentiles, the report gives **longest API stall** — the largest gap
between consecutive served probes. A blocked event loop shows up there even when
no single request looks slow, because the prober simply stops being served.

**Resources** are sampled per process at 1 Hz from inside the container. This is
per-process on purpose: every service runs as a sibling process in one
container, so `docker stats` on that container cannot tell connector from
indexing. The stateful sibling containers (Arango/Neo4j, Qdrant, Redis, Mongo)
*are* sampled with `docker stats`, since those are genuinely separate.

**Flame graphs** come from py-spy in a **separate profiled iteration**, never
during the measured window — profiling perturbs what it measures. Besides the
SVG, the ordered sample stream yields the **longest uninterrupted frame**: the
longest run of consecutive samples that never left one frame. On a single-loop
service that run *is* the stall, and it names the function responsible.

## Scope, stated plainly

With `enable_manual_sync` on, the sync task enumerates metadata, permissions,
and graph writes, but **never downloads file content** — that happens in
indexing, via `stream_record`. So this harness measures **enumeration and
graph-write throughput**. A change that only speeds up downloads will correctly
show as a no-op here. That is the right scope for connector-service scaling, but
do not read the number as end-to-end ingestion.

The report fails a run loudly if any record escapes with an indexing status
other than `AUTO_INDEX_OFF`, because that means the filter did not apply and the
indexing service was competing for the same CPU.

## Reproducibility

Perfectly identical results are not achievable. What the harness targets is low
enough variance that a real change is visible, and honesty about when it is not:

- **Fixed corpus.** Same `(units, scale, seed)` produces byte-identical objects
  with identical names and sizes.
- **Warmup discarded**, `repeat` measured iterations, median reported.
- **Coefficient of variation is a first-class result.** Above 5% the report says
  the run is not reproducible enough to A/B, and `compare` refuses a verdict.
- **Constant observer cost.** The `/stats` poll is itself load on the service
  under test, so it is held at one fixed rate by a single poller — a constant
  offset rather than run-to-run noise.
- **Connectors are deleted between iterations** and the harness waits for the
  graph cascade, so one iteration's cleanup is not charged to the next.

If CV is stubbornly high: raise `scale` (longer runs dilute fixed costs), raise
`repeat`, quiet the host, or pin the app container's CPU/memory so host
variation matters less.

## Scenarios

| scenario | source | shape | use |
|---|---|---|---|
| `smoke` | minio | 1 x 200, no profile | prove the wiring, ~2 min |
| `fleet` | minio | 8 x 2500 | concurrent syncs, the scenario the worker split exists to improve |
| `confluence-sweep` | confluence | N x one space | concurrency saturation sweep; `--units` is the knob |

`fleet` carries the same total record count as a single-connector run of the
same size, which is what isolates the cost of concurrency from the cost of
volume.

Override without editing files: `--units`, `--scale`, `--repeat`, `--warmup`,
`--seed`, `--no-profile`.

`--repeat 0` is legal only while profiling is on — profile-only mode, for when
the flame graph is the deliverable and there is nothing to average.

## Load sources

`python loadtest/run.py sources` lists what is registered.

Adding one is a single file under `lt/sources/` that subclasses `LoadSource`,
implements `seed()`, and registers itself — the runner needs no changes. Two
rules:

- Send `MANUAL_INDEX_OFF` in the filters (the base class helper does this via
  `Unit.filters_with_manual_index_off()`), or the run is not connector-only.
- Set `deterministic = False` for anything backed by an external SaaS API.
  Those runs are useful as realistic smoke tests but are not comparable between
  runs, and `compare` will refuse to grade them.

Currently shipped:

| source | connector | backing service | notes |
|---|---|---|---|
| `minio` | MinIO | `compose/minio.yml` | object-store enumeration; one record per object. The only **deterministic** source, so the only one `compare` will give an A/B verdict on. |
| `confluence` | Confluence | live SaaS | read-only, `deterministic = False`. All units share one space; `scale` is ignored because the space decides the record count. |

Planned: `gitlab` (self-managed CE — note the CE container's initial root
password expires after 24h, so it needs a `gitlab-rails runner` reset plus
seeded projects), then the non-deterministic `s3` (real AWS) and `slack`.

Note on S3: the `s3` connector has no endpoint override, so it only ever talks
to real AWS. The `minio` connector is the same `S3CompatibleBaseConnector`
subclass and does accept an endpoint, which is why MinIO gives you the S3 code
path reproducibly.

## Both deployments

Works against the prebuilt-image compose and the hot-reload dev compose. Process
discovery matches on cmdline and skips the `watchmedo` supervisor that wraps
each service in the dev image, so dev-mode measurements are not double-counted.
Confirm what it found with `python loadtest/run.py doctor`.

## Tests

```bash
python loadtest/tests/test_analysis.py
```

No docker or live stack required — covers the percentile/CV maths, the
validity gates, `/proc` field offsets, blocking attribution, and report
rendering.

## Requirements on the host running the harness

- Python 3.10+, `pip install -r loadtest/requirements.txt`
- Docker CLI with access to the daemon running the stack
- For flame graphs, py-spy inside the app container. The harness installs it on
  demand and attaches via `docker exec --privileged`, so the stack does **not**
  need restarting with extra capabilities — which matters, because the measured
  and profiled iterations must run against the same process.
