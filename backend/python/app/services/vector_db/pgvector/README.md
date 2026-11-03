# pgvector provider

The PostgreSQL + pgvector vector store. `PgVectorService` implements `IVectorDBService` for collections, the catalog and writes; reads, search
and the `VECTOR_DB_TYPE=pgvector` factory entry arrive in later changes, so until then nothing selects this package and the default vector store
(Qdrant) is unchanged. Schema: `backend/db/versions/retrieval/`.

| File | Role |
| --- | --- |
| `config.py` | `PgVectorConfig`: `dsn` (a `pipeshub_<service>_login`) from `/services/pgvector` or `PGVECTOR_DSN`, which wins; the jobs login only from `PGVECTOR_JOBS_DSN`; `PGVECTOR_STRICT`, `PGVECTOR_FENCE`, `PGVECTOR_PLACEMENT` (`own_leaf` or `none`). A stored null means the default |
| `tuning.py` | every search knob: typed, defaulted, ranged; a bad value falls back to the default with a warning |
| `scope.py` | the org of a call comes from the caller only; the ambient org is a cross-check (`OrgMismatch`); none or malformed is `OrgRequired` |
| `catalog.py` | collections and per-org embedding spaces through the vetted functions of `retrieval_0001`; every value is a bound parameter; creating or dropping a collection is a jobs path |
| `placement.py` | `PlacementPolicy` and the default `OwnLeafPolicy`: file a request for the org's own partition and wait for the placement worker before the first write; it never falls back to the shared tree. The worker is a separate change; until it ships, a first write with the default policy fails after `placement_wait` seconds (`PGVECTOR_PLACEMENT=none` is for tests) |
| `sql_filters.py` | `FilterExpression` to SQL, pure: the structure is built here, every value is a bound parameter |
| `writes.py` | upsert (one statement per batch: new points inserted, existing ones locked in key order and rewritten only when they differ, postings and the point-count delta following the rows that changed), batched delete keyed on `(org_id, point_sk)` (tombstones in HNSW safe mode), payload merges and the membership of a group behind a compare-and-set fence (`computed_at`) |
| `reads.py` | scroll (keyset on `point_sk`, opaque offset bound to the org), retrieve by id, count; every read is pinned to the org, the collection and the org's active space and skips tombstoned points |
| `visibility.py` | which points a search may return: the filter's membership arms parsed once (`scope_of`), as SQL fragments for the exact and the indexed tiers; an empty arm matches nothing, and a condition no search can express is refused rather than dropped |
| `probe.py` | flags from the PostgreSQL major and the pgvector version (below 0.7.0 refused; `PGVECTOR_STRICT` refuses below 0.8.2) |
| `errors.py` | typed errors; messages keep the tokens `CollectionRegistry` matches on |

Every write names its org (a point's `metadata.orgId`, a filter's `metadata.orgId`, or `org_id=`); the ambient org is only cross-checked. Searching is a separate change.

Tests: `tests/unit/services/vector_db/test_pgvector_*.py`; `tests/integration/vector_db/test_pgvector_*.py` against a migrated database through PgBouncer.
