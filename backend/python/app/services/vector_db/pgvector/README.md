# pgvector provider

The PostgreSQL + pgvector vector store. `PgVectorService` implements `IVectorDBService` for collections, the catalog and writes; reads, search
and the `VECTOR_DB_TYPE=pgvector` factory entry arrive in later changes, so until then nothing selects this package and the default vector store
(Qdrant) is unchanged. Schema: `backend/db/versions/retrieval/`.

| File | Role |
| --- | --- |
| `config.py` | `PgVectorConfig`: `dsn` (a `pipeshub_<service>_login`) from `/services/pgvector` or `PGVECTOR_DSN`, which wins; the jobs login only from `PGVECTOR_JOBS_DSN`; `PGVECTOR_STRICT`, `PGVECTOR_FENCE`, `PGVECTOR_PLACEMENT` (`own_leaf` or `none`). A stored null means the default |
| `tuning.py` | every search knob: typed, defaulted, ranged; a value that is not a number of the right kind or is outside its range is a startup error naming the variable (never silently the default: a value meant as off must not switch something on) |
| `scope.py` | the org of a call comes from the caller only; the ambient org is a cross-check (`OrgMismatch`); none or malformed is `OrgRequired` |
| `catalog.py` | collections and per-org embedding spaces through the vetted functions of `retrieval_0001`; every value is a bound parameter; creating or dropping a collection is a jobs path |
| `placement.py` | `PlacementPolicy` and the default `OwnLeafPolicy`: file a request for the org's own partition and wait for the placement worker before the first write; it never falls back to the shared tree. The worker is a separate change; without it a first write with the default policy fails after `placement_wait` seconds (`PGVECTOR_PLACEMENT=none` is for tests) |
| `sql_filters.py` | `FilterExpression` to SQL, pure: the structure is built here, every value is a bound parameter |
| `writes.py` | upsert (one statement per batch: new points inserted, existing ones locked in key order and rewritten only when they differ, postings and the point-count delta following the rows that changed), batched delete keyed on `(org_id, point_sk)` (tombstones in HNSW safe mode), payload merges and the membership of a group behind a compare-and-set fence (`computed_at`) |
| `probe.py` | flags from the PostgreSQL major and the pgvector version (below 0.7.0 refused; `PGVECTOR_STRICT` refuses below 0.8.2) |
| `errors.py` | typed errors; messages keep the tokens `CollectionRegistry` matches on |

Every write names its org (a point's `metadata.orgId`, a filter's `metadata.orgId`, or `org_id=`); the ambient org is only cross-checked. Writing, reading and searching are separate changes; this package writes.

Tests: `tests/unit/services/vector_db/test_pgvector_*.py`; `tests/integration/vector_db/test_pgvector_*.py` against a migrated database through PgBouncer.
