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
| `reads.py` | scroll (keyset on `point_sk`, opaque offset bound to the org), retrieve by id, count; every read is pinned to the org, the collection and the org's active space and skips tombstoned points |
| `visibility.py` | which points a search may return: the filter's membership arms parsed once (`scope_of`), as SQL fragments for the exact and the indexed tiers; an empty arm matches nothing, and a condition no search can express is refused rather than dropped |
| `dense.py` | the dense leg. `Sizer` counts the filter's candidates (up to 4 x `n_e0`, reused 60 s) and finds the org's leaf; `choose` picks the tier: **e0** exact up to `n_e0` candidates (40,000), **h** bit HNSW + halfvec rerank of `max(800, 20 x leg)` for an org that owns its leaf and has more than `n_own` candidates making up at least 10 % of the leaf (or more than `n_e0`), or that holds under 5 % of a shared leaf, **e0x** (exact up to 4 x `n_e0`) or **h_deep** (`max(4000, 50 x leg)`) for an org that awaits promotion. A tier takes a transaction-scoped advisory-lock slot (class 7370) so the cap holds across processes and poolers; no slot, a tier timeout or an unreadable sizing is answered by the bounded exact path (at most `degraded_rows` of the newest candidates), counted, logged and flagged on every result (`SearchResult.degraded`) |
| `fusion.py` | reciprocal-rank scores, `1 / (2 + index)` per leg, ties by id, as Qdrant's server-side fusion computes a one-leg answer |
| `sampler.py` | the online recall sampler: about 1 % of dense legs are re-run exactly over the same candidates after the answer is on its way (bounded queue, rate limit, throttled to 5 % of the cores); never changes an answer |
| `validate.py` | `bit_recall`: recall of bit HNSW + rerank against exact search over stored vectors used as queries; the first time an org's space is served by the vector index it runs in the background and the verdict is stored per org and space (`retrieval.space_checks`); below `validate_floor` (0.95) the space is served by a bounded path and flagged. `PgVectorService.validate_space` runs it on demand |
| `probe.py` | flags from the PostgreSQL major and the pgvector version (below 0.7.0 refused; `PGVECTOR_STRICT` refuses below 0.8.2) |
| `errors.py` | typed errors; messages keep the tokens `CollectionRegistry` matches on |

Every write names its org (a point's `metadata.orgId`, a filter's `metadata.orgId`, or `org_id=`); the ambient org is only cross-checked. A search names its org in its filter; an empty accessible list finds nothing. The lexical leg and the hybrid fusion are a separate change.

Tests: `tests/unit/services/vector_db/test_pgvector_*.py`; `tests/integration/vector_db/test_pgvector_*.py` against a migrated database through PgBouncer.
