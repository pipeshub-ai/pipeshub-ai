# pgvector provider

The building blocks of the PostgreSQL + pgvector vector store. The provider class that implements `IVectorDBService` and the
`VECTOR_DB_TYPE=pgvector` factory entry arrive in later changes; until then nothing selects this package and the default vector
store (Qdrant) is unchanged. Schema: `backend/db/versions/retrieval/`.

| File | Role |
| --- | --- |
| `config.py` | `PgVectorConfig`: `dsn` (a `pipeshub_<service>_login`) from `/services/pgvector` or `PGVECTOR_DSN`, which wins; the jobs login only from `PGVECTOR_JOBS_DSN`; `PGVECTOR_STRICT`, `PGVECTOR_FENCE`, `PGVECTOR_PLACEMENT` (`own_leaf` or `none`). A stored null means the default |
| `tuning.py` | every search knob: typed, defaulted, ranged; a bad value falls back to the default with a warning |
| `scope.py` | the org of a call comes from the caller only; the ambient org is a cross-check (`OrgMismatch`); none or malformed is `OrgRequired` |
| `catalog.py` | collections and per-org embedding spaces through the vetted functions of `retrieval_0001`; every value is a bound parameter; creating or dropping a collection is a jobs path |
| `placement.py` | `PlacementPolicy` and the default `OwnLeafPolicy`: file a request for the org's own partition and wait for the placement worker before the first write; it never falls back to the shared tree. The worker is a separate change; until it ships, a first write with the default policy fails after `placement_wait` seconds (`PGVECTOR_PLACEMENT=none` is for tests) |
| `probe.py` | flags from the PostgreSQL major and the pgvector version (below 0.7.0 refused; `PGVECTOR_STRICT` refuses below 0.8.2) |
| `errors.py` | typed errors; messages keep the tokens `CollectionRegistry` matches on |

Tests: `tests/unit/services/vector_db/test_pgvector_core.py` and `test_pgvector_placement.py`.
