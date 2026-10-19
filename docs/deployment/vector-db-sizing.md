# Sizing the vector database (Qdrant)

Search stays fast only while the parts of the index that every query touches fit
in Qdrant's memory. This page says how much memory that is for a given corpus,
what happens below it, and how PipesHub warns you.

## Why memory decides search speed

PipesHub stores every large Qdrant structure on disk and memory-maps it, so a
collection never makes Qdrant run out of memory. Speed is the cost instead. Each
query walks four structures:

- the **quantized vectors**, a compact int8 copy of every vector that the
  nearest-neighbour search compares against;
- the **HNSW graph**, the links between neighbouring vectors;
- the **sparse (keyword) index**;
- the **payload indexes** on the fields every search filters on (organization,
  connector, record group, record).

The operating system keeps recently read file pages in its page cache. Under a
container memory limit, that cache shares the limit with Qdrant's own process
memory. When the four structures fit in what is left, queries are served from
memory. When they do not, each new query reads them back from disk, and queries
running at the same time wait on that disk.

The full-precision vectors are not part of this working set. A query reads only
the few dozen it rescores, so they can stay on disk without slowing search.

## How much memory

```text
memory needed ≈ points × (dimensions + 374 bytes) + 2 GiB
```

- **points** is the number of chunks in the vector database. One record usually
  becomes several chunks. The warning described below reports the current count.
- **dimensions** is the embedding size: 1024 for the default local model
  (`BAAI/bge-large-en-v1.5`), 1536 for OpenAI `text-embedding-3-small` and
  `text-embedding-ada-002`, 3072 for `text-embedding-3-large`.
- **374 bytes per point** covers the rest of the working set: 4 bytes of scalar
  quantization overhead, about 60 for the HNSW graph (at the default `m=16`),
  about 50 for the sparse index, and about 260 for the payload indexes
  (about 65 bytes for each indexed field a point carries, four on average).
- **2 GiB** is Qdrant's own process memory on a large collection.

The per-point figures were measured on a 5.08 million-point collection with 1536
dimensions:

| Part of the working set | Size on disk | Per point |
| --- | --- | --- |
| Quantized vectors (int8) | 7.8 GiB | 1,640 bytes |
| Payload indexes | 1.24 GiB | 263 bytes |
| HNSW graph | 0.28 GiB | 58 bytes |
| Sparse index | 0.23 GiB | 48 bytes |
| Qdrant itself | about 2 GiB | — |
| **Total** | **about 11.5 GiB** | |

The full-precision vectors of the same collection took 31 GiB, and the whole
collection 46 GiB on disk; neither number decides speed.

### Memory needed by corpus size

| Points (chunks) | 1024 dimensions | 1536 dimensions | 3072 dimensions |
| --- | --- | --- | --- |
| 100,000 | 2.1 GiB | 2.2 GiB | 2.3 GiB |
| 500,000 | 2.7 GiB | 2.9 GiB | 3.6 GiB |
| 1,000,000 | 3.3 GiB | 3.8 GiB | 5.2 GiB |
| 2,000,000 | 4.6 GiB | 5.6 GiB | 8.4 GiB |
| 5,000,000 | 8.5 GiB | 10.9 GiB | 18.0 GiB |
| 10,000,000 | 15.0 GiB | 19.8 GiB | 34.1 GiB |
| 20,000,000 | 28.0 GiB | 37.6 GiB | 66.2 GiB |

Set Qdrant's memory limit to at least this figure, and leave room for the corpus
to grow. Read it the other way to see what a limit holds:

| Qdrant memory limit | 1024 dimensions | 1536 dimensions | 3072 dimensions |
| --- | --- | --- | --- |
| 4 GiB | 1.5M points | 1.1M points | 0.6M points |
| 6 GiB | 3.1M points | 2.2M points | 1.2M points |
| 8 GiB | 4.6M points | 3.4M points | 1.9M points |
| 12 GiB | 7.7M points | 5.6M points | 3.1M points |
| 16 GiB | 10.7M points | 7.9M points | 4.4M points |

With more than one Qdrant node, each node needs memory for the shards it holds.
A collection with one shard sits whole on one node however many nodes there are.

## What happens below it

Nothing fails; search slows down, and it slows down more under load. Measured on
the 5.08 million-point collection above, which needs about 11.5 GiB:

| Qdrant memory limit | One search at a time | Three at once | Six at once |
| --- | --- | --- | --- |
| 4 GiB | 2.28 s | 6.35 s | 14.7 s |
| 12 GiB | 0.86 s | 1.44 s | — |

These are the times of the semantic search stage alone. At 4 GiB only about
2 GiB of page cache was left, and every new query read about 400 MB from disk.

## Setting the limit

### Docker Compose

`docker-compose.yml` reads Qdrant's limit from `QDRANT_MEMORY_LIMIT` and passes
the same value to the PipesHub container, so the warning below knows the real
figure. Set it in `.env`:

```bash
QDRANT_MEMORY_LIMIT=12G
```

then recreate the containers (`docker compose up -d`). If you change the limit by
editing the compose file or an override instead, set `QDRANT_MEMORY_LIMIT` to the
same value, or the warning compares against the wrong figure.

### Helm

Set `qdrant.resources.limits.memory`. The chart passes the same value to the
PipesHub pod as `QDRANT_MEMORY_LIMIT`. The limit applies to each Qdrant pod.

## The warning on the Services page

**Workspace → Services** shows a warning under the vector database when the
estimate above is larger than the memory Qdrant has:

> The search index needs about X GB of memory to stay fast; the vector database
> has about Y GB, so searches read from disk and slow down further when several
> run at once.

The same report is at `GET /api/v1/health/vector-db/memory` (org admins only). It
never changes any service's health status.

Where the figures come from:

- **What the index needs** comes from Qdrant's collection information: the point
  count, the vector size, the quantization settings, the HNSW `m` and the number
  of points in each payload index, multiplied by the per-point figures above.
  It lands within about 5% of the files on disk, on the low side, because points
  deleted but not yet cleaned up are not counted.
- **What Qdrant has** is `QDRANT_MEMORY_LIMIT`. Qdrant cannot report its own
  container limit (its telemetry reports the host's RAM), and PipesHub cannot
  read another container's limit, so the deployment states it. Without it, the
  host's RAM is the only figure. That can prove a shortfall (the index is bigger
  than the whole machine) but never that memory is enough, so with only that
  figure the page warns or stays silent; it never reports "enough".

There is no warning while the index itself is under 1 GiB, whatever the limit,
so small installs never see one.

## Keeping the quantized vectors in RAM (`always_ram`)

Qdrant can hold the quantized vectors in its own process memory instead of the
page cache (`always_ram: true` in the collection's quantization config). They
then never leave memory, but that memory cannot be reclaimed: if the limit is
too small, Qdrant is killed for running out of memory instead of slowing down.
PipesHub creates collections with `always_ram: false` for that reason.

Turning it on with Qdrant's update-collection API makes Qdrant rebuild the
quantized data of every segment in the background: heavy disk I/O, and
temporarily more disk space while old and new segments coexist. If
`VECTOR_STORAGE_RECONCILE_ENABLED=true` is set, PipesHub resets the setting to
`false` on its next storage reconcile.

Raising the memory limit to the figure above gives the same speed without the
out-of-memory risk, so prefer that.
