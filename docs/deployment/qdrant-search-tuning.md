# Tuning Qdrant search accuracy

PipesHub searches Qdrant with two legs, a dense (embedding) leg and a keyword
leg, and fuses them with reciprocal rank fusion. The dense leg is approximate:
it walks an HNSW graph and scores candidates on an int8 copy of the vectors
before re-scoring the best ones with the full-precision originals. Three
settings control how much accuracy that approximation gives up. The defaults
are the values that measured best; the environment variables below change them
without a rebuild.

## Query-time settings

| Variable | Default | What it does |
| --- | --- | --- |
| `QDRANT_HNSW_EF` | `256` | How many graph candidates a search keeps (the HNSW beam). Higher is more accurate and a little slower. `0` uses the collection's own default, which is the `ef_construct` it was built with (100 on collections built before this setting existed). |
| `QDRANT_OVERSAMPLING` | `1.5` | How many times the requested depth the int8 scan passes to the full-precision re-score. `1` re-scores only the requested depth. |

Both apply to the dense leg only and take effect on the next search. A value that
is not a finite number in range (`QDRANT_HNSW_EF` 0-4096, `QDRANT_OVERSAMPLING`
1-4, `QDRANT_HNSW_EF_CONSTRUCT` 4-4096) is ignored with a warning and the default
is used.

What changed, measured on 100,785 block-level points of five public retrieval
benchmarks (1024-dimensional `bge-large-en-v1.5`, three independently built
collections, 3,677 queries):

- The dense leg recovered 99.9% of the exact top 40 (97.1% before). On the
  hardest benchmark slice the dense-only nDCG@10 loss against exact search
  fell from about 10% to about 3.7%.
- The fused (hybrid) ranking, which is what users see, was already within
  0.1-0.2% of exact search and stays there: no setting lost accuracy.
- Searches restricted to one organization or scope are exact in practice.
  Qdrant scans a filtered set of up to about 20,000 vectors directly instead
  of walking the graph, and the larger candidate pool removes the remaining
  int8 ordering errors (recall 0.97-0.98 before, 1.000 after).
- Latency: about +1 % warm p95 at one client and about +5 % at 16 concurrent
  clients. On a cold cache the one-client p95 rose from 8.9 ms to 10.4 ms
  (+17 %), because the wider beam and the extra re-scored vectors are read from
  disk.

Memory: re-scoring reads about 50% more full-precision vectors per query. They
are memory-mapped, so this shows up as page cache, not process memory, but
under a tight memory limit it means a few more disk reads per search (see
[sizing the vector database](vector-db-sizing.md)). Set `QDRANT_OVERSAMPLING=1`
to turn it off.

## Build-time setting

| Variable | Default | What it does |
| --- | --- | --- |
| `QDRANT_HNSW_EF_CONSTRUCT` | `256` | Candidates considered per link when the graph is built. A better graph finds more of the true neighbours at no cost per search. |

This applies to collections created after it is set. A collection that already
exists keeps the value it was built with; a new embedding model or a rebuild of
the vector store creates the collection again and picks up the new value. On
the benchmark above the same collection built with 256 instead of 100 lost
about 1.5% dense-only nDCG on the hardest slice, against about 10% before,
with the same build time, disk size and search latency. Going to 32 links per
node (`m`) gave no further gain.

## Stable result order

Reciprocal rank fusion gives equal scores to a dense-only hit and a
keyword-only hit of the same rank, and Qdrant returns equal scores in a
different order on each call. In a test of 300 queries run twice against the
same collection, 84% returned a different top 20 order and 13% a different top
20 set, although each leg alone was identical across runs. PipesHub now asks
Qdrant for two extra results, sorts by score and then point id, and keeps the
requested number. After that the two runs were identical for all 300 queries.
This makes the order of ties stable; it is not a guarantee. The extra margin covers
ties that reach two results past the cut, and a longer run of equal scores at the
cut is still trimmed by Qdrant in its own order before the sort.
