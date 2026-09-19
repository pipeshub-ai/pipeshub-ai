# Performance benchmarks

A repeatable measurement of how fast PipesHub indexes documents, and a check
that tells you when that gets noticeably worse. Indexing is the first thing
measured here; the layout leaves room for query and connector sync later.

| File | What it is |
| --- | --- |
| `corpus.py` | Builds the synthetic documents. Same seed, same files. |
| `bench_indexing.py` | Uploads the corpus into a fresh knowledge base and times the indexing. |
| `compare.py` | Judges a result against a committed baseline. Reports only. |
| `baselines/<label>.json` | Committed results, one per environment. |
| `../../.github/workflows/perf-indexing.yml` | Runs the benchmark every week on the CI stack. |

It lives in `integration-tests/` rather than `loadtest/` because it drives the
same API the integration tests do. It reuses their knowledge-base client
(`helper/clients/kb_client.py`), their login and OAuth-client bootstrap, and
their AI-model seeding, and CI already installs their dependencies. `loadtest/`
is a separate Locust tool for query and chat load with its own auth code.

## What it measures

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

Indexing needs an LLM as well as an embedding model. With no LLM configured,
every record ends `FAILED` with the reason "Failed to process document:
'llm'". With no embedding model configured, the stack's built-in CPU model is
used, which works but is slow.

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

The check does not fail the workflow. It writes its verdict into the job
summary and exits 0. The thresholds above are reasoned, not yet measured. Once
four or five weekly runs exist, look at how much they actually vary, adjust the
numbers, and consider passing `--fail-on-regression`. If the baseline and the
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
changes what is being measured, so it needs a new baseline in the same PR.

`baselines/ci-neo4j-4cpu.json` is a placeholder for now. It holds a note
instead of numbers, so the workflow reports each run and says there is nothing
to compare with yet. Replace it with the first trusted scheduled run, as above.
A 50-file run on a developer laptop was tried first and did not give a usable
baseline. The machine was busy with other stacks, and the laptop's LLM ran on
its CPU. After an hour, 22 of the 47 uploaded files were indexed and the rest
were still in progress.
