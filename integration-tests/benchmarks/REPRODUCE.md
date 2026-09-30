# Reproducing the FRAMES results

This walks through reproducing, on one machine, the full FRAMES board: every
RAG technique and PipesHub's agent loop on all 824 questions, graded by two
LLM judges, with every correct answer checked against the evidence the system
was shown. Expect most of a day of wall-clock time; nearly all of it is
indexing and model calls, and every stage resumes where it stopped.

The published numbers, with the exact configs and summaries they came from,
are in [`datasets/frames/results/`](datasets/frames/results/).

## What is pinned

| What | Pin | Where |
| --- | --- | --- |
| Questions | `google/frames-benchmark` at commit `58d9fb6330f3ab1316d1eca12e5e8ef23dcc22ef`, `test.tsv` sha256-verified | `datasets/frames/loader.py` |
| Dev / held-out split | 200 / 624, seeded, stratified; file sha256 `fe4fe15c6965…` | `datasets/frames/data/split_v1.json` |
| Corpus | 12,441 Wikipedia articles (2,517 gold + 10,000 distractors from their outlinks), each at its last revision on or before 2024-10-15; corpus version `a89e6af19589…` | built by the `corpus` stage |
| Grader and answer prompts | copied verbatim where published, sha256-pinned | `harness/grading/prompts/`, `ANSWER_PROMPT_PINS` |
| Models | answerer `gpt-5.6-luna` (reasoning effort high), embedding `text-embedding-3-small`, judges `claude-sonnet-5` and `gemini-3.8-flash` | the run configs |
| PipesHub | built from the commit you check out; the image carries its sha as the `git.sha` label, and each run records the checkout's sha in `run_meta.json` | step 2 |
| Costs | one price table in each config, applied to token counts every system reports | `pricing:` in the configs |

Every run directory records its resolved config, a hash of everything that
affects what it measured (`config.hash`; resuming with a different config is
refused), and in `run_meta.json` the dataset revision and sha256, the split's
sha256, the corpus version, the model names and the answer prompts' hashes.
Compare those first when two runs disagree.

## Prerequisites

- Docker with at least 10 CPUs and 16 GB of memory for its VM, and about
  60 GB of free disk. The published runs used Docker Desktop on an Apple
  silicon Mac with those limits.
- Python 3.12 and [`uv`](https://github.com/astral-sh/uv).
- API access:
  - Azure OpenAI with deployments named `gpt-5.6-luna` and
    `text-embedding-3-small`. Other providers work by changing `answerer:`
    and `embedding:` in the configs, but then the numbers are not comparable.
  - Anthropic and Google Gemini keys for the two judges.

## 1. Install the harness

```bash
cd integration-tests
uv venv && uv pip install -e .
.venv/bin/python -m pytest unit/frames -q    # the harness's own tests
```

Put secrets in `integration-tests/.env.local` (never in a config), and set
`PIPESHUB_TEST_ENV=local` in `integration-tests/.env` so it is loaded:

```bash
PIPESHUB_BASE_URL=http://localhost:3000
PIPESHUB_TEST_USER_EMAIL=...        # becomes the benchmark org's admin
PIPESHUB_TEST_USER_PASSWORD=...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_VERSION=...
TEST_ANTHROPIC_API_KEY=...
TEST_GEMINI_API_KEY=...
QDRANT_API_KEY=...                  # same value as in deployment/docker-compose/.env
```

## 2. Build and start PipesHub

From the repository root, on the commit you want to measure:

```bash
docker build \
  --build-arg PYTHON_DEPS_IMAGE=pipeshubai/pipeshub-ai-base:python-deps-slim \
  --label "git.sha=$(git rev-parse HEAD)" \
  -t pipeshubai/pipeshub-ai:frames .

cd deployment/docker-compose
cp env.template .env        # then set QDRANT_API_KEY and the other secrets it lists
IMAGE_TAG=frames COMPOSE_PROFILES=graph-neo4j docker compose -p pipeshub-ai \
  -f docker-compose.yml \
  -f docker-compose.benchmark.override.yml \
  -f docker-compose.benchmark.ports.yml \
  up -d
```

`docker-compose.benchmark.override.yml` turns off code execution and skills,
so PipesHub has no tool the other systems lack beyond its calculator (see
"No cheating" in the [README](README.md)). `docker-compose.benchmark.ports.yml`
publishes Qdrant and the connector service on localhost, which the RAG
baselines and the harness read. The app container starts its services one
after another; the next step waits until all of them are healthy.

## 3. Prepare the stack

```bash
cd integration-tests
.venv/bin/python -m benchmarks.harness setup-pipeshub \
  --config benchmarks/datasets/frames/configs/full-pipeshub.yaml
```

This creates the org with the benchmark user as admin, marks onboarding done,
turns off user and org context in prompts, registers `text-embedding-3-small`
as the default embedding model (or checks that the one configured is it), and
registers the answerer and both judges. It is safe to run again.

## 4. Run the board

Three runs share one stack and one index. A run asks its systems one after
another, so the two RAG runs go side by side.

```bash
CFG=benchmarks/datasets/frames/configs
# First: builds the corpus, uploads it to PipesHub, waits for indexing, builds
# the standard 512/64 index, then asks the controls and nine RAG pipelines.
.venv/bin/python -m benchmarks.harness run --config $CFG/full-rag.yaml

# Once full-rag's log shows "stage prepare: processed", in other terminals:
.venv/bin/python -m benchmarks.harness run --config $CFG/full-rag-rerank.yaml
.venv/bin/python -m benchmarks.harness run --config $CFG/full-pipeshub.yaml
```

The reranking pipelines load a cross-encoder locally, so `full-rag-rerank`
is CPU-bound on the host. If you start the second and third runs before the
first has finished preparing, they will upload a second copy of the corpus.

Each run writes to `reports/frames-benchmark/<run id>/`. If a run stops, for
example a laptop sleeps or a rate limit trips the circuit breaker, continue
it:

```bash
.venv/bin/python -m benchmarks.harness run --config $CFG/full-rag.yaml --resume <run id>
# add --retry-errors to re-ask questions whose answer failed
```

### How long, and what it costs

Measured on the machine described under Prerequisites:

| Stage | Wall-clock |
| --- | --- |
| Corpus fetch from the MediaWiki API (first time only; cached in `.cache/frames/corpus`) | hours, rate-limited by Wikipedia; resumable |
| Upload and PipesHub indexing of 12,441 articles | ~4.5 h |
| Standard 512/64 index | ~1 h |
| `full-rag` asking (11 systems × 824) | ~3.5 h |

Per-system cost and tokens are in each run's `summary.json` and in
[`datasets/frames/results/`](datasets/frames/results/). Judge calls are
cached in `.cache/frames/llm_cache.sqlite`, so re-grading costs nothing.

## 5. Read the results

`report.md` in each run directory is the board: accuracy with 95% bootstrap
confidence intervals, grounded accuracy (correct and supported by the
system's own evidence), memory-suspect answers, a dev versus held-out split,
retrieval recall, citation checks, cost, latency and paired McNemar tests.
`summary.json` holds the same numbers for tooling.

To regrade or re-verify an existing run without asking again:

```bash
.venv/bin/python -m benchmarks.harness grade  --config <config> --resume <run id>
.venv/bin/python -m benchmarks.harness verify --config <config> --resume <run id>
```

## What will and won't match

- **The questions, split, corpus and prompts match exactly**: each is pinned
  by hash and recorded in `run_meta.json`. The corpus version is a hash of its
  manifest, so a Wikipedia fetch that differs in any article shows up there.
- **Answers will differ somewhat between runs.** The answering model is a
  reasoning model whose sampling temperature is fixed by the provider, so it
  is not deterministic. On the 200-question dev split, repeating an identical
  PipesHub run moved accuracy by up to 4 points; on 824 questions expect
  roughly ±2 points. Compare systems with the paired tests in the report,
  not by the last digit.
- **Judges are deterministic given their inputs** (temperature 0, cached),
  and two independent judges are reported with their agreement (Cohen's κ).
- **Model versions behind a name can change.** Record the dates of your runs;
  the published runs' dates are in their results folders.
