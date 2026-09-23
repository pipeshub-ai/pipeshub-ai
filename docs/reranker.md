# Reranker

Vector and keyword search find candidates quickly but order them roughly. A
reranker (a cross-encoder, or a hosted rerank API) reads the query and each
candidate together and scores how well the candidate answers it. PipesHub can
reorder search results with one before the agent reads them. It is off by
default and turned on per organization in **Labs → Enable Reranker**
(`ENABLE_RERANKER`).

## Where it runs

In the query service, inside the one pipeline every retrieval entry point
shares (`app/modules/retrieval/context/builder.py`):

```text
hits -> units -> rank and keep the best -> ±1 neighbours -> graph context -> reading order
```

The rank step is a `UnitRanker`:

- `RelevanceRanker` orders by retrieval score. Used while the flag is off.
- `RerankingRanker` (`context/reranking.py`) sends the 64 best retrieval
  candidates to the reranker, orders them by reranker score alone and keeps 15
  (or fewer if the caller asked for fewer). Neighbours and the reading order
  are applied afterwards, so the model still reads each record's blocks in
  document order, most relevant record first.

The search tool ranks against its own query (the hop the agent is on); the
first-turn prefetch ranks against the user's question. `fetch_record` is never
reranked: it returns a whole record and would overflow the context.

The text a reranker scores is the record's name, a code block's qualified
name, then the unit's text (a table's summary and rows; an image's
description, never its bytes), cut to 4,000 characters.

## Failure is never an error

Search must not depend on the reranker. `RerankingRanker` falls back to
retrieval order when the call fails, returns nothing usable, or takes longer
than 5 seconds. `RerankerResolver` (`app/modules/reranker/resolver.py`) returns
no reranker, and search carries on, when the flag or the model config cannot
be read or the config is invalid. Both log a warning.

## Choosing the model

AI Models → **For Reranking**. Providers:

| Provider | Runs | Notes |
|---|---|---|
| Default (`defaultReranker`) | Local model server | `BAAI/bge-reranker-v2-m3`, multilingual; used when the flag is on and nothing is configured |
| Sentence Transformers, Hugging Face | Local model server | Any cross-encoder on the Hub, e.g. `BAAI/bge-reranker-base` |
| Cohere, Jina AI, Voyage | Hosted API | Vendor rerank endpoints |
| OpenAI Compatible, LiteLLM Proxy | Your gateway | `POST {endpoint}/rerank`, Cohere/Jina request shape |

Saving a reranker runs a health check: the passage that answers a probe
question must rank first, otherwise the model is rejected as a config error.
Local models download through the same progress dialog as embedding models;
the model server loads them as `CrossEncoder`s (`POST /v1/rerank`).

The flag and the model config are read on every request, so changes apply to
the next search. No event is published for reranker changes.

## Images

The full image bakes `BAAI/bge-reranker-v2-m3` (~2.3 GB) into the Hugging Face
cache (`BUNDLE_RERANKER=1` in `Dockerfile.base`). Slim images build with
`BUNDLE_RERANKER=0`; the model downloads the first time it is used, and
searches keep retrieval order until it is ready.

## Telemetry

Each record in a `retrieval_context` stream frame carries `rerankScore`, its
best reranker score, when it was reranked.
