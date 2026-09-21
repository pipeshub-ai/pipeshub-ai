# RAG benchmark harness

Runs a set of retrieval systems over one corpus with the same answering model
and the same frozen prompts, and reports accuracy with confidence intervals
next to what each answer cost in tokens, dollars and latency.

```
benchmarks/
  harness/           the engine: stages, scoring, metrics, report, adapters
  datasets/frames/   one benchmark: Google's FRAMES (824 multi-hop questions)
```

## Adding a dataset

Implement `DatasetPlugin` (`harness/datasets.py`) and register it. It answers
the four things the engine cannot know:

| Method | Question it answers |
| --- | --- |
| `load` / `revision` / `split_path` | where the questions come from, and what pins them |
| `normalize_ref` | when two gold refs name the same document |
| `corpus_source` | how documents are fetched (`None` if the dataset ships them) |
| `judges` / `primary_rubric` | how an answer is graded correct |

Nothing under `harness/` should need to change; a test asserts the engine
never imports a dataset. See `datasets/frames/plugin.py` for the reference
implementation.

## Quick start

Run from `integration-tests/` against a running stack (see
`.github/workflows/frames-benchmark.yml` for the CI bring-up).

```bash
# Secrets (env or .env.local — never in YAML):
export PIPESHUB_TEST_USER_EMAIL=... PIPESHUB_TEST_USER_PASSWORD=...
export TEST_OPENAI_API_KEY=... TEST_ANTHROPIC_API_KEY=... TEST_GEMINI_API_KEY=...
export PIPESHUB_BASE_URL=http://localhost:3000 PIPESHUB_CONNECTOR_URL=http://localhost:8088

python -m benchmarks.harness seed-models --config benchmarks/datasets/frames/configs/smoke.yaml
python -m benchmarks.harness run --config benchmarks/datasets/frames/configs/smoke.yaml
```

Results land in `reports/frames-benchmark/<run_id>/`: `report.md` (the board),
`summary.json`, `failures.csv`, plus the raw `predictions.jsonl`,
`judgments.jsonl`, `claims.jsonl`, `rankings.jsonl` and `scores.jsonl`.

| Command | Does |
|---|---|
| `run` | dataset → corpus → prepare (ingest + index gate) → ask → search → grade → score → report |
| `run --resume RUN_ID` | continue a stopped run; every stage skips work already recorded |
| `run --resume RUN_ID --retry-errors` | also re-ask questions whose prediction failed |
| `grade` / `score` / `report --resume RUN_ID` | re-derive from recorded predictions (score/report are offline) |
| `corpus` | build the pinned corpus only |
| `seed-models` | register the configured answerer and judges in PipesHub if missing |
| `write-split` | regenerate `data/split_v1.json` (committed; do not change casually) |

Exit codes: `0` valid, `1` error, `2` completed but **invalid** (a disallowed
tool was called).

## What is fixed, and where

- **Dataset**: `google/frames-benchmark` at commit `58d9fb63…`, `test.tsv`
  sha256-verified (`dataset/loader.py`). URL quirks are normalised in
  `dataset/urls.py`.
- **Split**: 200 dev / 624 held-out, seeded and stratified by reasoning-type
  combination (`data/split_v1.json`). Iterate on dev; score held-out only at
  milestones.
- **Corpus**: each article pinned to its last revision at or before
  2024-10-15 via the MediaWiki API, cleaned to HTML with tables kept and images,
  navboxes and reference sections removed (`corpus/`). Tier `G` is the gold
  articles; `GD` adds seeded hard-negative distractors from the gold pages'
  outlinks. `corpus_version` hashes the manifest.
- **Prompts**: the FRAMES auto-rater (paper Figure 6) and the SimpleQA grader
  (simple-evals, MIT) are copied verbatim and sha256-pinned
  (`grading/prompts.py`).
- **Models**: identities come from PipesHub's model registry; provider keys
  from the environment. Judges run at temperature 0 and are cached.

## No cheating

- PipesHub is only called with `chatMode: "internal_search"` (no web tools).
- The benchmark stack runs with `PIPESHUB_ENABLE_CODE_EXECUTION=false` and
  `PIPESHUB_AGENT_DISABLED_TOOLSETS=calculator,date_calculator,image_generator,artifacts`
  (`deployment/docker-compose/docker-compose.benchmark.override.yml`), so the
  agent has no compute tools the baselines lack.
- `guard.py` checks every tool call in every trace against an allowlist of
  knowledge/planning tools; anything else makes the run **invalid**.
- Baselines and judges never receive tools or web grounding.
- Systems never see gold answers (`AskItem` has no answer field).

## Metrics

| Group | Metric |
|---|---|
| Answer | FRAMES accuracy (primary judge, bootstrap CI), secondary-judge accuracy and κ, SimpleQA strict accuracy, hedge rate, leniency gap |
| Retrieval | all-gold-in-context, context recall, surfaced recall (from the `retrieval_context` stream frames); recall@k / MRR / nDCG@10 from ranked search |
| Citations | integrity (markers resolve, records exist, text matches source), precision/recall vs gold articles, ALCE citation recall/precision, correct ∧ grounded |
| Diagnosis | failure signature per wrong answer (`metrics/failures.py`): system error, policy violation, abstained, F6 reasoning miss, F3 context overflow, F2 turn budget, F5 links unused, F1 stopped early, F4 never surfaced |
| Ops | latency p50/p95, cost and cost per correct answer |

## RAG approaches (`naive_rag`, `advanced_rag`)

Both read the Qdrant points PipesHub wrote for the benchmark KB (same chunks),
embed queries with the model PipesHub indexed with (`embedding:`, checked
against PipesHub's default at startup), and answer with the same `answerer`
at the same `reasoning_effort`. Hybrid search is PipesHub's own query: dense +
`Qdrant/bm25` prefetches at `2 × limit`, fused with RRF.

| Option (`advanced_rag`) | Values |
|---|---|
| `retrieval` | `dense` · `sparse` · `hybrid` |
| `transform` | `none` · `expansion` (`n_queries`, RRF-merged) · `decomposition` (`max_subquestions`, round-robin merged) |
| `rerank` | cross-encoder (`reranker`, default `BAAI/bge-reranker-v2-m3`) over `candidate_k` (default `2 × top_k`) |
| `top_k` / `small_to_big` | chunks in context / full text for the N best articles |

`naive_rag` is dense top-`k` and accepts only `top_k`. Query transforms are
LLM calls on the answering model and count toward the system's tokens; the
reranker runs locally and costs latency, not tokens.

**Token cost** is recorded per LLM call for every system (PipesHub reports its
calls in the opt-in `run_usage` stream frame, including context-compaction
calls) and priced from the config's `pricing:` table, never a provider SDK's.
Cost columns stay blank unless every answered prediction reported usage.

Qdrant is reached at `PIPESHUB_QDRANT_URL` (default `http://localhost:6333`)
with `QDRANT_API_KEY`; on the default compose file publish it with
`deployment/docker-compose/docker-compose.benchmark.ports.yml`. Azure models
need `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`
(or their `TEST_` forms).

## Adding a system (next round: Onyx, RAGFlow, Dify, LightRAG)

Implement `systems/base.py::SystemAdapter` (and `CorpusIngestor` /
`RankedRetriever` where supported) in a new package under `systems/`, then add
one `AdapterSpec` to `systems/__init__.py::ADAPTER_REGISTRY`. No stage changes.

## Tests

```bash
pytest unit/frames -q
```
