# Performance benchmarks

Repeatable measurements of how PipesHub behaves under load, and checks that
tell you when that gets noticeably worse. Five questions are asked here: how
fast does it index (the weekly indexing benchmark), how fast does it answer
(the weekly query benchmark), does search and chat keep working when many
people use it at once for a while (the weekly sustained-load test), does it
hold up at a large customer's volume (the monthly scale run), and does it lose
anything when pushed past its limits (the stress run).

| File | What it is |
| --- | --- |
| `corpus.py` | Builds the synthetic documents. Same seed, same files, whatever the corpus size. Can hand them over in batches. |
| `stack.py` | Shared plumbing: log in, create a knowledge base, upload the corpus, wait for the indexer. |
| `bench_indexing.py` | Uploads the corpus into a fresh knowledge base and times the indexing. |
| `bench_query.py` | Seeds a knowledge base, then times searches and chat turns under load. |
| `bench_load.py` | Seeds a knowledge base, then ramps users up through searches and chat, holds them, and fails the run on errors. |
| `bench_scale.py` | Indexes a much larger corpus, reporting how the run changed as it went. |
| `bench_stress.py` | Uploads far faster than the stack can index, then checks nothing was lost. |
| `bench_harness.py` | Agent harness SLO #2: the durable route's non-model latency against the legacy route, on one stack, with a scripted model. Other profiles: the latency gates per mode on the fault-stack rig (`harness_rig.py`, `harness_profiles.py`, `slo_gates.py`). |
| `scale_metrics.py` | The arithmetic behind those two: slices of a run, drift, overload verdicts. |
| `compare.py` | Judges a result against a committed baseline. Reports only. |
| `bench_costclass.py` | Record event to indexed per corpus kind (small text, small PDF, office, scanned, large, huge), open-loop, on local CPU embedding with a fake chat model. |
| `salt.py` | Per-upload content salts, so the org-wide md5 dedup never sees the same bytes twice. |
| `fake_ai/server.py` | The fake OpenAI-compatible chat and vision provider, with an optional quota that answers 429. |
| `tiers.py`, `run_tiers_e2e.sh` | Pick idle cores, check a tier fits, refuse a busy host, run the bench over app tiers. |
| `../../deployment/docker-compose/docker-compose.bench.yml` | The resource-pinned stack override (cpus, disjoint cpusets, memory) the cost-class bench runs on. |
| `../../.github/workflows/perf-costclass.yml` | Nightly smoke, weekly 4-CPU run, and dispatchable tiers. |
| `baselines/<label>.json` | Committed results, one per environment and benchmark. |
| `../../.github/workflows/perf-indexing.yml` | Runs the indexing benchmark every week on the CI stack. |
| `../../.github/workflows/perf-query.yml` | Runs the query benchmark every week on the CI stack. |
| `../../.github/workflows/perf-load.yml` | Runs the sustained-load test every week on the CI stack. |
| `../../.github/workflows/perf-scale.yml` | Runs the scale run monthly; stress and soak on request. |
| `../../.github/workflows/perf-harness.yml` | Runs the harness overhead benchmark every week on the `eval` stack. |

It lives in `integration-tests/` rather than `loadtest/` because it drives the
same API the integration tests do. It reuses their knowledge-base client
(`helper/clients/kb_client.py`), their login and OAuth-client bootstrap, and
their AI-model seeding, and CI already installs their dependencies. `loadtest/`
is a separate diagnostic toolkit for a person comparing two builds by hand:
flame graphs, container probes and per-phase timing. This measures the numbers
a schedule can watch; that one explains them.

## What the indexing benchmark measures

The corpus is a mix of `txt`, `md`, `html` and `csv` files, plus `docx`, `xlsx`
and text-only `pdf` files. Three quarters are under 20 KB, a fifth are 20 to 100
KB, and one in twenty is between 100 KB and 5 MB. File and folder names and the
text include accented letters, Cyrillic, Arabic, Hebrew, CJK and emoji. The
files sit in a nested folder tree. The default is 500 files from seed 1337,
about 38 MB.

The benchmark:

1. creates a knowledge base, recreates the folder tree and uploads every file
   through `POST /api/v1/knowledgeBase/{kb}/upload`, four at a time;
2. polls the knowledge base's record list every 2 seconds until every record
   reaches a final indexing status, or an hour passes;
3. deletes the knowledge base and any AI models it added.

| Measure | Meaning |
| --- | --- |
| Wall time | From the first upload to the last record finishing. |
| Throughput | Records that reached `COMPLETED`, per minute of wall time. |
| Time to indexed p50/p95/p99 | For each completed record, from its upload returning to the first poll that saw it `COMPLETED`. Accurate to about one poll interval. |
| Failures | Upload errors, records that ended `FAILED`, `EMPTY` or another non-success final status, records still unfinished at the timeout, and records the listing never showed. If only never-shown records are left five minutes after their upload (`--not-listed-grace`), the run stops early and says so, instead of waiting out the hour. The result lists up to 50 failures with the indexer's reason. |
| Peak memory | The highest `docker stats` reading for the app container, and the highest RSS of the indexing process tree inside it (read with `docker top`, so the image needs no `ps`). The app container runs all seven services, so the second figure is the one about indexing. Both need `--container`. |

Every file carries a per-run line of text, so each run uploads new bytes. The
indexer skips files whose MD5 it has already indexed, and a rerun against the
same stack would otherwise measure that shortcut.

A file's name, kind, size and content come from its own stream, keyed by the
seed and the file's position, so file 7 is the same file whether 100 or 100,000
were asked for. The folder tree is the exception: it grows with the corpus, so
a bigger run has more folders and a file can land in a different one.

## Running it locally

You need a running stack you can throw away. Use your own compose project name
and port so you do not touch anyone else's containers:

```bash
cd deployment/docker-compose
cp env.template perf.env        # then set passwords; COMPOSE_PROFILES=graph-neo4j; APP_PORT=3710
docker compose -p pipeshub-perf --env-file perf.env up -d
```

Create an org and skip onboarding as in the "Create organization" and "Skip
onboarding" steps of `.github/workflows/perf-indexing.yml`. Then, from
`integration-tests/` with its virtualenv active:

```bash
export PIPESHUB_BASE_URL=http://localhost:3710
export PIPESHUB_TEST_USER_EMAIL=... PIPESHUB_TEST_USER_PASSWORD=...
python perf/bench_indexing.py --docs 100 --label my-laptop \
  --ai-models existing --container pipeshub-perf-pipeshub-ai-1 \
  --summary reports/perf/summary.md
python perf/compare.py --baseline perf/baselines/my-laptop.json --current reports/perf/indexing.json
docker compose -p pipeshub-perf down -v   # when finished
```

The query benchmark takes the same arguments, plus the shape of the load:

```bash
python perf/bench_query.py --docs 40 --users 2 --duration 60 --label my-laptop-query \
  --ai-models existing --container pipeshub-perf-pipeshub-ai-1 \
  --summary reports/perf/summary.md
python perf/compare.py --baseline perf/baselines/my-laptop-query.json --current reports/perf/query.json
```

Start small locally: 40 files still have to be indexed before the first
question, and chat turns on a laptop's own model take far longer than the
provider models CI uses.

Indexing needs an LLM as well as an embedding model. With no LLM configured,
every record ends `FAILED`, with a reason saying no AI model is set up for the
workspace. With no embedding model configured, the stack's built-in CPU model
is used, which works but is slow.

- `--ai-models seed` (the CI default) adds the test OpenAI LLM and embedding
  models for the run and removes them afterwards. It needs `TEST_OPENAI_API_KEY`.
- `--ai-models existing` uses whatever the org already has. Without a key, a
  local Ollama model works. Add it as the org's default LLM in the AI models
  settings, with endpoint `http://host.docker.internal:11434`.

The result records the LLM and embedding model that were used. `compare.py`
treats runs with different models as not comparable.

Spreadsheets (`csv`, `xlsx`) are the slowest kind by far, because the indexer
sends table rows through the LLM, up to `MAX_TABLE_ROWS_FOR_LLM` per file. On
a laptop CPU model, one spreadsheet can take a quarter of an hour. Pass
`--kinds txt,md,html,docx,pdf` to leave them out of a local run. The kinds are
recorded and compared like the other settings.

To look at a corpus without a stack: `python perf/corpus.py --docs 50 --out /tmp/corpus`.

## What the query benchmark measures

The same corpus, smaller: 120 files by default, seeded and indexed before
anything is timed. Then a fixed profile — four simulated users for five
minutes, each repeating the same cycle of operations one second apart:

| Operation | What it does |
| --- | --- |
| `search` | `POST /api/v1/search` for one of eight phrases, no filter. Twice per cycle. |
| `search_filtered` | The same, filtered to the seeded knowledge base. Once per cycle. |
| `chat` | `POST /api/v1/conversations/stream`, read to the terminal frame. Once per cycle. |

Each user starts at its own offset in the question list, so four users are not
asking the same thing at the same moment. The questions are built from the
corpus's own vocabulary, so a search has something to find; a question nothing
can answer would measure the empty-result path and look fast.

| Measure | Meaning |
| --- | --- |
| Latency p50/p95/p99 | Per operation, for the requests that succeeded. A chat turn is timed from the request to the terminal `RUN_FINISHED` frame. |
| Chat first answer frame | How long a turn takes to start answering: the first frame carrying answer text. Not the first frame of the stream — the gateway flushes one as soon as the conversation row exists, before the query service has been asked anything, so timing that would miss every change in retrieval and prompt assembly. |
| Throughput | Operations completed per minute of the load window. |
| Errors | Refused requests, streams that ended without an answer, and `RUN_ERROR` frames, grouped by reason. |
| Found something | The share of successful requests that came back with anything: a search with hits, an answer citing at least one record. A run where searches find nothing is measuring the empty-result path, however fast it looks. |
| Peak memory | The highest `docker stats` reading for the app container during the load. Needs `--container`. |

The question set, the mix, the number of users, the duration and the think time
all go into the result, and `compare.py` refuses to judge two runs that differ
in any of them.

Seeding needs the corpus indexed, so a query run costs an indexing run first.
If the indexer does not finish in `--index-timeout` (30 minutes by default), or
fewer documents are indexed than `--require-indexed` asks for (all of them by
default), the benchmark stops before asking anything and says how many were
indexed and what the rest ended as. A run that gets past that but is still short
of its baseline's corpus is reported without a verdict. Questions asked over a half-seeded
knowledge base come back empty, which is faster and counts as a success
everywhere, so an unfinished seed would otherwise read as the best run yet.

## What the sustained-load test measures

The query benchmark above asks four users for five minutes and reports. It
cannot say whether search and chat keep working, and keep their speed, when
more people use them at once for longer, which is what a busy morning at a
customer looks like. `bench_load.py` asks that, every Wednesday
(`.github/workflows/perf-load.yml`), and unlike the benchmarks it can fail the
job.

It seeds 80 files, without spreadsheets (they index slowest, and this measures
questions, not indexing), and waits for all of them to be indexed, the same way
the query benchmark does. Then it runs **stages**. Each stage moves the number
of active users in a straight line from the previous stage's count to its own,
over its length, the way k6's `ramping-vus` stages work. Stages are written
`seconds:users`, comma-separated and without spaces; the workflow's input check
and the parser accept exactly the same values. The default,
`120:4,120:8,900:8`, ramps to four users over two minutes, to eight over the
next two, and holds eight for fifteen. A user the ramp drops finishes the
operation it is in first.

Each user repeats one cycle of six operations: a plain search, a streaming chat
turn, a search filtered to the seeded knowledge base, a non-streaming chat turn
(`POST /api/v1/conversations/create`), another search and another streaming
turn. Between operations it waits a random one to three seconds, like Locust's
`between(1, 3)`, from a random sequence fixed by the seed, so two runs wait the
same way. Real users pause; without the pause a handful of simulated users
behave like hundreds hammering the stack, which measures a queue rather than a
service.

| Measure | Meaning |
| --- | --- |
| Latency p50/p95/p99 | Per operation, for the requests that succeeded. A streaming turn is timed to its terminal frame, and a non-streaming one to its response. A non-streaming response that carries no answer counts as a failure, not a fast success. |
| Time to first answer | For streaming turns, how long until the first frame carrying answer text, as in the query benchmark. |
| Throughput | Operations per minute, and separately the successful ones per minute, so a stack that fails fast does not look busier than one that answers. |
| Error rate | Failed operations over all operations: refusals, cut streams, `RUN_ERROR` frames, empty answers. |
| By stage | The same numbers for each stage, so a latency that climbs with the users shows where it started. |

The headline numbers are for the **steady window**: the stages that hold the
peak user count. The ramp stages mix different loads and move whenever the ramp
changes, so they are shown per stage but never compared with a baseline.

### When the run fails

The run applies its own checks, which need no baseline and so count from the
very first run:

- the error rate over the whole run is at most 2% (`--max-error-rate`);
- the steady window measured at least 50 operations, so its percentiles mean
  something (`--min-operations`);
- every kind of operation succeeded at least once in the steady window;
- plain searches found something, and so did searches filtered to the seeded
  knowledge base, checked separately. An empty result is fast and counts as a
  success, so a run that found nothing measured the not-found path, and a
  filter that stopped matching would hide behind plain searches that still
  find plenty.

If at least half of the last 30 operations failed (`--abort-error-rate`), the
run stops early rather than spending the rest of its time and AI budget on a
broken stack, and fails. This is k6's `abortOnFail`.

The result and summary are written before the job fails, so a failed run still
shows what it measured.

Then `compare.py --fail-on-regression` puts the steady window next to the
committed baseline. Two kinds of row fail the job. The **p95 latencies** of
search, filtered search, streaming turn, first answer and non-streaming turn
fail it when one rises more than 30% **and** by more than a fixed amount
(0.25 s for searches, 0.5 s for the first answer, 1 s for a whole turn). The
**share of searches, plain and filtered, that found a hit** fails it on any
fall: the corpus and the questions are fixed, so a search that stops finding
its documents is a fault, not noise. The fixed amount matters on short
requests: a 100 ms search that becomes 150 ms is 50% slower and is still only
a shared runner's jitter. Throughput, the median, the error rate and the share
of answers citing a document are shown and flagged, marked "reported only":
they move with the model provider from week to week.

`baselines/ci-load-neo4j-4cpu.json` is a placeholder until a trusted run
replaces it (see "Updating a baseline" below). Until then the comparison
reports each run and passes, and only the run's own checks can fail it.

To run it by hand, with the same setup as the query benchmark:

```bash
python perf/bench_load.py --docs 40 --stages 60:2,60:4,300:4 --label my-laptop-load \
  --ai-models existing --container pipeshub-perf-pipeshub-ai-1 --fail-on-violation \
  --summary reports/perf/summary.md
```

Cost on CI: about 19 minutes of load, most of it at eight users. With turns
taking several seconds and one to three seconds of thought between operations,
that is roughly 600 chat turns a week, plus the embeddings for 80 files. The
job's time limit is two hours: about 15 minutes to build and start the stack,
up to 30 to seed, and the load.

## What the harness overhead benchmark measures

`bench_harness.py` answers one question from
`docs/agent-harness-v2/SLO.md` (#2): how much slower is an agent turn on the
durable harness route than on the legacy route, leaving out the model?

- **One stack, both routes, one job.** The org's `ENABLE_AGENT_HARNESS` Labs
  flag is switched between blocks of samples (`--block`, 20 by default), so a
  busy runner slows both routes alike. Legacy is
  `POST /api/v1/agents/{agent}/conversations/stream`; the harness route is
  what the browser does with the flag on: `POST /api/v1/agents/{agent}/runs`,
  then the run's SSE stream.
- **A scripted model in the benchmark process.** It is an OpenAI-compatible
  server the stack reaches at `--model-url` (by default
  `host.docker.internal:<port>`). Every call waits `--model-latency-ms` to its
  first byte and streams the answer in chunks `--chunk-interval-ms` apart. Every
  call is timed on the benchmark's clock and filed under a tag carried in the
  question, so "minus the model" subtracts what the model actually took.
- **The `short-turn` profile.** One question, one read tool (the toolset
  catalog, `list_toolsets`, which every agent has), one streamed answer. That
  is two model calls on both routes.

| Measure | Meaning |
| --- | --- |
| TTFT, non-model | Request sent to the first `TEXT_MESSAGE_CONTENT` frame, minus the model's time until it wrote its first answer chunk. |
| Turn done, non-model | Request sent to the root `RUN_FINISHED` frame, minus all the model's time. |
| Gate | Absolute budget (SLO #2): harness non-model p50 ≤ legacy + `--ttft-budget-ms` (75) at TTFT and ≤ legacy + `--done-budget-ms` (100) at turn done; with `--container`, each durable commit kind's p50 ≤ `--commit-budget-ms` (25). The ratio is reported for information. `--fail-on-gate` makes the exit status say so. |
| Breakdown | With `--container`, the harness lifecycle logs of the benchmark's runs: each event's `ms` field (commits by kind, claim, admission) and each event's offset from `admission.accepted`. |

Admission is limited to 30 runs a minute per user, so the benchmark waits out a
`429` and asks again; 200 samples per route took 10 to 20 minutes on a laptop. Run it
against a stack started from `docker-compose.yml` plus
`docker-compose.agent-harness.yml` (durable runs need the replica set):

```bash
python perf/bench_harness.py --base-url http://localhost:3000 \
  --email admin@example.com --password '...' --bootstrap \
  --samples 200 --label my-laptop-eval --container pipeshub-ai-pipeshub-ai-1 \
  --output reports/perf/harness-short-turn.json
python perf/compare.py --baseline perf/baselines/my-laptop-eval-harness-short-turn.json \
  --current reports/perf/harness-short-turn.json
```

### Interactive session ready under saturation (`saturate`, SLO #20)

`--profile saturate` runs on the same stack and scripted model. It fires `--background` scheduled runs (each its
own webhook schedule, fired once; Labs `ENABLE_SCHEDULED_RUNS` is switched on for the org), whose model asks the read
tool `--background-steps` times at `--background-latency-ms` a call. Once as many have started as the stack runs at
once, it admits `--samples` interactive turns (default 12), one every `--turn-interval` seconds, each on its own
thread. Session ready is admission to the turn's first model call, both on this process's clock. The gate is SLO
#20: p95 within `--ready-slo-ms` (5000) and every scheduled run completed. The result also reports how many scheduled
runs the model saw at once and the most calls it served at once, so a run that did not saturate the stack, or a
provider limit that did not hold, shows in the numbers. No paid model is called.

Saturate the stack on purpose: start it with a small provider limit and a small worker, for example
`MODEL_GATEWAY_PROVIDER_CONCURRENCY=4` on the `pipeshub-ai` service and the worker's container limited to four
slots' worth of CPU and memory. Defaults stay within the admission quota (30 runs a minute per user); a refused
admission is waited out and is not counted in session ready.

```bash
python perf/bench_harness.py --profile saturate --base-url http://localhost:3000 \
  --email admin@example.com --password '...' --label my-host --fail-on-gate \
  --output reports/perf/harness-saturate.json
```

## Harness latency gates per mode (the fault-stack rig)

`bench_harness.py --profile <name>` with any profile but `short-turn` runs on the fault-stack rig
(`harness_rig.py`): the production Node harness composition (`fault-stack-server.ts`) and worker
(`integration-tests/harness/fault/worker.py`) with a scripted model at a fixed token rate, so every number is the
harness's. It needs Docker, `npm ci` in `backend/nodejs/apps` and the backend's Python (the worker runs
`app.harness_main`); no stack, no secrets.

| Profile | Measures | SLO |
|---|---|---|
| `modes` | `conversation_created`, admission, first token, each delta and the hand-off, for agent quick, auto, react, deep and the assistant's internal_search and web_search | #3, #4, #5 |
| `cancel` | Stop at a random point of a long answer and of a slow tool, to the run's last frame | #7 |
| `reconnect` | drop the stream mid-answer, reconnect with `Last-Event-ID`, to the first frame | #8 |
| `code-mode-5k`, `commits-n16` | commit latency per kind, commits per turn, journal bytes per run | #1, #9, #10 |
| `mcp-5-servers` | first token with 5 MCP servers discovered per run, against none | #12 |
| `sandbox` | Docker broker, acquire to a finished command, cold and warm | #17 |
| `approval`, `deep-wave` | "Allow" to the write running; a wave's spawn and join | — |

```bash
python perf/bench_harness.py --profile rig --samples 100 --label my-host \
    --output reports/perf/harness-latency.json --fail-on-gate
```

`--profile rig` runs every profile a gate reads. The rig brings up the fault compose project (replica set and Redis)
on `--port-base`; `--mongo-uri`, `--redis-port` and `--redis-db` use a running replica set and one Redis DB instead.
The rig refuses a DB that already holds keys and clears only that DB when it is done. The gates are `slo_gates.py`: one row per SLO, a threshold with headroom under the SLO target
or a written reason. A run whose load average went above the core count reports `rerun`; a missed SLO with a
recorded finding reports `waived`. `baselines/<label>-harness-latency.json` holds a reference run's statistics.

## Scale, stress and soak runs

These answer different questions from the weekly benchmark, and they cost more,
so they run monthly or on request: **Scale and Stress**
(`.github/workflows/perf-scale.yml`), with a `mode` of `scale`, `stress` or
`soak`.

### Scale — does it hold up as the corpus grows?

`bench_scale.py` indexes a far larger corpus and reports the same totals as the
weekly benchmark, plus the shape of the run: throughput per slice, median
time-to-indexed per slice, and the indexing service's memory from start to end.
A run that indexes 90 files a minute at the start and 20 at the end passes every
average and is still a problem, and the slice table is where that shows.

The verdict above the table says either that everything held steady or what did
not, in words. Two deliberate quietenings, so it reports real problems rather
than arithmetic:

- speed is judged only over slices where files were still **queued**. Every run
  empties its queue at the end, and that idle tail is not a slowdown;
- a longer time-to-indexed is only called out when the **queue was not growing**
  underneath it. A file that waits behind a longer queue takes longer to index,
  and that is queueing, not a fault.

Files are generated and uploaded in batches (`--batch-size`, 250 by default) and
their bytes dropped once uploaded, so the run holds one batch of content at a
time rather than the whole corpus. What does grow with `--docs` is the plan —
one small record per file, naming it and its size — and the per-record
bookkeeping of what was uploaded and when. Those are bytes per file rather than
kilobytes, which is what makes a six-figure corpus possible at all.

### Stress — does it lose anything when overloaded?

`bench_stress.py` fires the whole corpus at the upload API at once, with far more
parallel uploads than the indexer can keep up with, so work piles up. It is not a
speed measurement. Afterwards it answers five questions:

| Under overload | Why it matters |
| --- | --- |
| Was every upload either accepted or refused with an error? | Refusing is a fine answer. Accepting and losing it is not. |
| Did every accepted file appear in the knowledge base? | Catches a file that vanished between the API and the store, and duplicates. |
| Did every one of them finish, one way or another? | Catches records left in flight for good. |
| Were the refusals the stack shedding load rather than breaking? | Under overload we expect 429s and 503s. A dropped connection or a 500 is a different thing. |
| Did the backlog clear once the load stopped? | Catches a queue that never drains. |

A refused upload is not a lost file — the caller was told — so refusals do not
fail the "accepted or refused" check whatever their cause. How the stack
refused is its own question, and its own row above. If that row turns out to be
noisy on a shared runner, it is the one to revisit first.

A run where every upload was refused has no backlog to clear, and recovers
immediately rather than failing for never draining.

Unlike the comparison thresholds, these are correctness checks, so the workflow passes `--fail-on-violation` and
the job fails when one of them does.

### Soak — does memory creep up over hours?

The same scale run over more files, started by hand, where the thing to read is
the memory trend rather than the throughput. It is dispatch-only because it
holds a runner for hours; there is no separate harness for it.

### What fits on today's runner

These jobs use the same 4-CPU runner as the weekly benchmark. The defaults were
chosen to fit inside it: 2,000 files for a scale run (up to about four hours),
400 for a stress run (under an hour), 4,000 for a soak (up to about five hours).
AI usage is a few dollars for a scale run and well under a dollar for a stress
run.

The plan's target is **100,000 files**, which that runner cannot do inside a
job's time limit. The harness is built for it — content is held one batch at a
time, and what remains resident is a small record per file — so it is a
question of machine size and hours rather than code. On a bigger runner, raise
`docs` on the dispatch and `--timeout` in the workflow, and expect a fresh
baseline, because a run of a different size is not comparable with this one.

A run that hits its time limit fails rather than reporting on part of a corpus,
so a truncated scale run cannot be mistaken for a good one.

## The comparison and its thresholds

`compare.py` puts the result next to the baseline with the same label and marks
a measure as a regression when it moves past its threshold:

| Measure | Flags when | Why there |
| --- | --- | --- |
| Throughput | falls more than 20% | A whole-run rate averages out per-record jitter, so it is the steadiest number. Shared CI runners still vary by something like 10% between identical runs. 20% sits above that noise and below the size of the regressions worth catching, such as a lost worker, a stage that became serial, or an extra round trip per record. |
| Time to indexed p95 | rises more than 30% | The 95th percentile is decided by the slowest 25 of 500 records, mostly the large files, and it is only measured to within one poll. It moves more than throughput, so it gets more room. |
| Time to indexed p50 | rises more than 30% | Same reasoning as p95. |
| Wall time | rises more than 25% | Mostly tracks throughput. It shows the change in minutes a person would notice. |
| Peak indexing memory | rises more than 25% | Memory is steady from run to run, but the sampler reads it only every 5 seconds, so short spikes can be missed. |
| Failed or unfinished records | rises at all | The baseline should have none. A new failure is a correctness problem, not noise. |

The load test's checks are described in "When the run fails" above.

For a query run it checks these instead:

| Measure | Flags when | Why there |
| --- | --- | --- |
| Search p50 and p95, filtered search p95 | rise more than 30% | Searches are short, so a shared runner's jitter is a large share of one. 30% sits above that and below a regression worth catching, such as a lost index or an extra round trip per query. |
| Chat turn p50 and p95 | rise more than 30% | Most of a turn is the model provider's own latency, which varies from week to week whatever the code does. |
| Chat first answer frame p95 | rises more than 30% | Retrieval and prompt assembly happen before the first answer frame, so this moves when our code slows down rather than the provider. |
| Throughput (operations/min) | falls more than 20% | A whole-run rate averages out per-request jitter, so it is the steadiest number here too. |
| Searches that found a hit, filtered searches that found a hit, answers that cited a document | fall at all | An empty result is fast and counts as a success, so a run that stopped finding anything improves every latency measure above. The filtered searches get their own row because a knowledge-base filter that stopped matching would be hidden by the unfiltered ones still finding plenty. Any fall here is worth a look. |
| Failed searches and chat turns | rise at all | The baseline should have none. |

For a harness run it checks each route's TTFT p50 and p95 and turn-done p50
(non-model, rises more than 30%) and failed turns (rises at all). These catch a
route drifting from week to week; the SLO gate itself is the ratio inside one
run, which `bench_harness.py --fail-on-gate` enforces. Runs with a different
label, broker, model latency or chunk interval are not compared.

Scale results are judged by the indexing checks above, against their own
baseline (`ci-scale-neo4j-4cpu.json`), because they measure the same things on
a larger corpus. A scale run is never compared with an indexing run, and corpus
size is one of the fields that has to match, so a 2,000-file run is never put
beside a 100,000-file one. Stress results are not compared at all — their
verdicts are pass or fail, not faster or slower — and `compare.py` says so
plainly if one is handed to it.

For the indexing, query and scale benchmarks the check does not fail the
workflow. It writes its verdict into the job summary and exits 0. The
thresholds above are reasoned, not yet measured. Once four or five weekly runs
exist, look at how much they actually vary, adjust the numbers, and consider
passing `--fail-on-regression`. **Sustained Load** is the exception: it passes
`--fail-on-regression` already, so a gating row that regresses fails that
workflow (see "When the run fails" above), while its placeholder baseline
keeps the comparison reporting only until a real run replaces it. If the baseline and the
result differ in label, graph DB, broker, AI models, corpus size, seed or file
kinds, `compare.py` says so and ignores the verdicts.

## Updating a baseline, on purpose

A baseline changes only when the change is understood. Refreshing one to make
a warning go away hides the very thing this exists to catch.

1. Pick a run on `main` whose numbers you trust: a scheduled run, or a
   `workflow_dispatch` of **Indexing Performance**. Two runs that agree are
   better than one.
2. Download its `perf-indexing-<run id>` artifact and copy `indexing.json` to
   `integration-tests/perf/baselines/ci-neo4j-4cpu.json`. The file name is the
   `--label` the workflow passes.
3. Commit it on its own, and say why in the message. For example: "new
   baseline: the parser pool change makes indexing 30% faster".

Changing `--docs`, `--seed`, the corpus mix in `corpus.py` or the runner size
changes what is being measured, so it needs a new baseline in the same PR. For
the query benchmark the same is true of `--users`, `--duration`,
`--think-time` and the question set in `bench_query.py`: change any of them and
the next run will say it is not comparable until its baseline is refreshed.

The query baseline works the same way: take `query.json` from a trusted
scheduled run of **Query Performance** and copy it to
`integration-tests/perf/baselines/ci-query-neo4j-4cpu.json`. The load test's is
`load.json` from **Sustained Load**, copied to
`integration-tests/perf/baselines/ci-load-neo4j-4cpu.json`; changing
`--stages`, `--think-time` or its operation mix needs a new one, as above.

`baselines/ci-neo4j-4cpu.json` is a placeholder for now. It holds a note
instead of numbers, so the workflow reports each run and says there is nothing
to compare with yet. Replace it with the first trusted scheduled run, as above.
`baselines/ci-query-neo4j-4cpu.json` and `baselines/ci-load-neo4j-4cpu.json`
are placeholders too, for the same reason: a query benchmark needs its corpus indexed first, and a laptop's CPU
model leaves that unfinished. A 50-file run on a developer laptop was tried
first and did not give a usable baseline. The machine was busy with other stacks, and the laptop's LLM ran on
its CPU. After an hour, 22 of the 47 uploaded files were indexed and the rest
were still in progress.

## The cost-class benchmark

`bench_costclass.py` measures **record event to indexed** per corpus kind on the configuration that ships:
local CPU embedding, with only the chat and vision model faked (`fake_ai/server.py`). The cost-class
corpus (`backend/python/tests/perf/parse_bench/corpus.py`, profiles `cost-smoke`, `cost-standard`,
`cost-huge`) labels every file with its class, which is the oracle the results are reported by.

* **Latency** is `lastIndexTimestamp - scheduled_arrival_ms`. Uploads follow an open-loop schedule, so a
  slow stack cannot slow the arrivals, and a stalled upload counts its stall. `createdAtTimestamp` and the
  client's own timing are diagnostic columns.
* **Salts** (`salt.py`) put `run_id:seq` into every upload's extracted content (a PDF page, a Word
  paragraph, a slide run, a cell, a line), so no two uploads share an md5.
* **Accepted samples**: COMPLETED, one record per upload, a `virtualRecordId` no other record of the run
  shares, and plausible timestamps. Anything else is rejected with a reason; more than 1 % rejected makes
  the run **invalid**. `--inject-duplicate` re-uploads one file's exact bytes as a negative control and
  judges what the product did, not the label: the md5 dedup must give the copy the original's
  `virtualRecordId`. The same bytes under another vrid, indexed again, **fails** the run.
* **Invalid, failed, pass**: a sibling container above 70 % of its cpuset, or too many rejections, is
  *invalid* (exit 3): the numbers are kept and are not a result. A FAILED, missing or timed-out probe is
  *failed* (exit 1). Cache hits are a diagnostic and never reject a probe.
* **Scenarios** (`--scenario`): `smoke`, `b01` (huge PDFs plus small probes), `b02`, `b03`, `b05`,
  `b06` and `b06-knee` (small-only saturation), `calibrate`. Variants that change the run:
  `--fake-embeddings` (the embedding model faked too), `--fake-ai-base-ms N`, `--chaos kill-app-at=N`. The
  runner hands the first two to the fake provider's container (`BENCH_FAKE_AI_ARGS`) and the bench reads the
  running fake's configuration back, so a flag that did not take stops the run. `--chaos` kills the
  indexing process inside the app container (`kill -9`; `docker kill` would be a manual stop that no restart
  policy answers) and the container's process monitor restarts it. The verdict needs the kill to have fired and a
  new indexing process to be running by the end of the run (waited for up to a few minutes).
  Other stores, brokers, sources and replica counts are not built (the bench stack is Neo4j, Redis, upload,
  one app container) and have no flags.
* **Knee:** `b06-knee` runs one indexing process (`INDEXING_UVICORN_WORKERS=1`, checked in the container; a stack
  with more stops the run) at each tier. `--knee-from <result files>` (the runner calls it after the tiers) keeps
  valid one-process results and prints `CELL_CPUS` with a confidence: `high` when every tier has a point and a
  tier above the knee fell short, `medium` when the top tier still scaled (a lower bound), `low` when a tier is
  missing, the curve recovered after falling short, or the knee is the base tier. With `BENCH_REPEATS=N` a tier
  has several results, in any file order: its point is the **median** of its repeats, and a repeat that
  Docling's process capped is left out of the median and listed under `docling_capped_repeats` (a tier whose
  every repeat was capped makes the whole knee Docling-bound).
* **Baselines and repeats:** `BENCH_REPEATS=3` runs every tier three times, each on a fresh stack, then
  `--baseline-from <result files>` decides whether the repeats are a valid baseline: at least three, all passing,
  the p50 of every kind (and the throughput of a saturation burst) steady with a coefficient of variation of at
  most 10 %, and, when two burst tiers are present, throughput differing by at least 30 % between them. Anything
  else exits 3 with the reasons and the baseline is not committed. No baselines are committed yet: they need
  a quiet host.
* **Upload errors:** an exception inside the harness's own upload (a file it cannot read or salt) is reported as
  that upload's error with its cause; it fails the run.
* **Calibration** reads each family from its own process (`/proc/<pid>/stat` utime+stime of Docling's and the
  embedding server's process trees, sampled while one unit runs at a single worker). `s_d` is Docling's busy
  seconds per page. Parse, inspect and raster run inside the indexing process's pools and are reported **not
  measurable** until the pools report per-unit CPU; their design weights stay. Container CPU over a record's wall
  time is never used.
* **CI** (`.github/workflows/perf-costclass.yml`): the nightly `smoke`, and a Saturday `weekly` with one job per
  run, listed in `costclass_weekly_legs.json` (the control with the embedding model faked runs first, then B-05,
  B-03 and a scaled B-01, 4 CPUs each). Each job has its own build, start and bench limits, and
  `integration-tests/unit/test_perf_costclass.py` computes every leg's planned duration from its schedule and
  fails when it does not fit its step limit with 20 % to spare.
* **Smoke gates:** `--max-p95-s`, `--max-wall-s` and `--fail-on-log-errors` (any ERROR line of the app's log
  since the run started, minus `LOG_ERROR_ALLOW`, fails the run).

Run it on a quiet host, one tier at a time:

```bash
BENCH_TIERS="T4:4:16 T16:16:64" integration-tests/perf/run_tiers_e2e.sh --scenario b05 --dry-run   # the plan
BENCH_TIERS="T4:4:16" integration-tests/perf/run_tiers_e2e.sh --scenario smoke -- --inject-duplicate
```

The runner takes the idlest cores of the host for disjoint cpusets (app, Neo4j, Qdrant, Redis, Mongo, the fake
provider, and mailpit with minio), skips a tier that does not fit with a message, checks the host is under 85 % CPU
and memory before each tier, and removes each stack with `down -v`. A tier that fails does not stop the next;
the runner exits non-zero when nothing was measured or any tier failed, and says so for each tier it skipped.

**Not measurable on a 48-CPU shared host:** 48 and 96 CPU app tiers; NUMA; memory bandwidth at 96+ cores;
256 GB+ working sets; cgroup v1; Neo4j and Qdrant beyond 8 cores; real provider rate limits; HPA and KEDA.
Every result lists these.

**Large-host recipe:** `BENCH_TIERS="T16:16:64 T32:32:128 T48:48:192 T96:96:256"`, with the siblings scaled up
in `tiers.py` (`SIBLINGS_STANDARD`, `SIBLINGS_KNEE`) and the `--cpus`/memory of Neo4j and Qdrant raised in the
compose override. Run `b06-knee` first: its curve sets the one-process CPU count (`knee()` in
`bench_costclass.py`); `calibrate` measures cores per unit family and Docling's seconds per page.
