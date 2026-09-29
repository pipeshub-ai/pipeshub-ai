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

# The RAG baselines and the `prepare` vector check read Qdrant directly, and
# the compose stack runs it with auth on. Same value as QDRANT_API_KEY in
# deployment/docker-compose/.env — without it `prepare` dies on HTTP 401.
export QDRANT_API_KEY=...

python -m benchmarks.harness seed-models --config benchmarks/datasets/frames/configs/smoke.yaml
python -m benchmarks.harness run --config benchmarks/datasets/frames/configs/smoke.yaml
```

Results land in `reports/frames-benchmark/<run_id>/`: `report.md` (the board),
`summary.json`, `failures.csv`, plus the raw `predictions.jsonl`,
`judgments.jsonl`, `claims.jsonl`, `rankings.jsonl`, `scores.jsonl`,
`evidence.jsonl.gz` (the context each answer was produced from) and
`support.jsonl` (the evidence-support verdicts).

| Command | Does |
|---|---|
| `run` | dataset → corpus → prepare (ingest + index gate) → ask → search → grade → evidence → verify → score → report |
| `run --resume RUN_ID` | continue a stopped run; every stage skips work already recorded |
| `run --resume RUN_ID --retry-errors` | also re-ask questions whose prediction failed |
| `grade` / `score` / `report --resume RUN_ID` | re-derive from recorded predictions (score/report are offline) |
| `verify --resume RUN_ID` | evidence reconstruction + support judging only, for a run graded earlier (needs Qdrant for PipesHub evidence) |
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
  (`grading/prompts.py`), as are the harness's own claim-support and
  evidence-support grader prompts and every answering prompt.
- **Models**: identities come from PipesHub's model registry; provider keys
  from the environment. Judges run at temperature 0 and are cached.

## No cheating

- PipesHub is only called with `chatMode: "internal_search"` (no web tools).
- The benchmark stack runs with `PIPESHUB_ENABLE_CODE_EXECUTION=false` and
  `PIPESHUB_ENABLE_SKILLS=false`
  (`deployment/docker-compose/docker-compose.benchmark.override.yml`). PipesHub
  keeps its `calculator` and `date_calculator`: they do arithmetic over values
  already retrieved and cannot bring in outside information, and `guard.py`
  allows them explicitly. The other systems do the same arithmetic in-model.
- `guard.py` checks every tool call in every trace against an allowlist of
  knowledge/planning tools; anything else makes the run **invalid**.
- Baselines and judges never receive tools or web grounding.
- Systems never see gold answers (`AskItem` has no answer field).

### Answers must come from the corpus, not the model's training data

**Prompt-level grounding.** Every system that retrieves is told to answer
only from what it was shown: the RAG baselines with `rag-answer-v2`, oracle and
BM25 with `frames-answer-grounded-v1` (both: "Answer ONLY from the sources …
do not use [your training data]"), PipesHub by its own `internal_search`
prompt. Closed book keeps the ungrounded prompt on purpose: it is the
memory-only control.

**Judge-time verification.** A prompt is a request, not a guarantee, so every
answer the primary judge marks correct is also checked against the context
its system actually showed the answering model:

1. *Evidence* (`evidence.jsonl.gz`, one record per prediction and answer
   sha). Captured at ask time where the system's prompt is visible: the RAG
   baselines, oracle and BM25 record the exact numbered sources / articles
   block of their prompt, after fitting and small-to-big; Open WebUI and
   RAGFlow record the chunk texts they return (`unavailable` when they return
   none, e.g. RAGFlow's research loop); closed book records `empty`.
   The `evidence` stage rebuilds the rest from Qdrant, marked `reconstructed`:
   - PipesHub streams block ids, not text (`systems/pipeshub/evidence.py`):
     search-hit blocks, fetched ranges `startBlock … startBlock +
     blocksRendered − 1`, the record summary on a summary hit, and a record's
     block-less table points when a block-less hit or a rendered fetch could
     have shown them; sentence points are copies and are skipped.
   - RAG answers asked before capture existed (`systems/rag/evidence.py`):
     the recorded `retrieved` chunks are read back from the index the system
     searched (PipesHub's points, or the standard collection by point id) and
     run through the answerer's own source assembly, so small-to-big articles
     come back whole from the corpus. A test pins the rebuild to equal the
     ask-time capture.

   When Qdrant is unreachable the evidence is `unavailable` and is retried on
   the next `--resume`; the run never fails for it. Each record is capped at
   500k chars (`truncated`, original size and sha256 are kept).
2. *Evidence support* (`verify` stage, `support.jsonl`). A judge at
   temperature 0, cached, reads the question, the answer and the evidence
   under the pinned `evidence-support-v1` prompt and says whether the facts
   the answer depends on are stated in it: `SUPPORTED`, `PARTIAL` or
   `UNSUPPORTED`. Combining stated facts and arithmetic or date math over
   stated values count as supported. Evidence over the budget is cut
   deterministically: split into paragraph segments, ranked by BM25 against
   the question and answer, and the best kept with the segment before and
   after each; the cut is recorded. Unavailable evidence is `NO_EVIDENCE`
   without a call; empty evidence is `UNSUPPORTED` without a call. Wrong
   answers are not checked. Before judging, the stage logs a projected cost
   from the `pricing:` table; the spend counts toward `limits.max_cost_usd`.

Settings (`grading.evidence_support`, all optional; left out of the config
hash so a run started before the check can still be resumed and verified):

| Key | Default | Meaning |
|---|---|---|
| `judge` | `primary` | `primary`, `secondary`, or a model selector |
| `max_evidence_tokens` | `6000` | evidence shown per check |
| `systems` | all but `closed_book` | system ids to verify |

Changing the judge or the budget re-verifies on the next resume. An existing
run is verified with `verify --resume RUN_ID` (or `grade --resume`, which also
runs both stages).

## Metrics

| Group | Metric |
|---|---|
| Answer | FRAMES accuracy (primary judge, bootstrap CI), secondary-judge accuracy and κ, SimpleQA strict accuracy, hedge rate, leniency gap |
| Grounding | grounded accuracy = correct ∧ `SUPPORTED` (bootstrap CI; paired exact McNemar), memory-suspect = correct ∧ `UNSUPPORTED` (count, rate with CI, question ids), `PARTIAL` and `NO_EVIDENCE` counts. Rates are withheld until every correct answer has a verdict |
| By split | accuracy, grounded accuracy and memory-suspect rate per split of the dataset's committed split file (dev / held-out), with CIs, when a run spans more than one |
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
| `rerank` | cross-encoder (`reranker`, default `cross-encoder/ms-marco-MiniLM-L-6-v2`, the one PipesHub ships) over `candidate_k` (default `2 × top_k`) |
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
For the evidence check, set `Prediction.evidence` (`harness/evidence.py`) to
the context the adapter's model was given; if only ids are known at ask time,
give the spec a `rebuild_evidence` hook instead.

## Tests

```bash
pytest unit/frames -q
```
