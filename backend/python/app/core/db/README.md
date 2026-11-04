# Database runtime (`app/core/db`)

An asyncpg runtime for PostgreSQL behind a connection pooler in transaction mode. Nothing in the app constructs it
unless a feature selects PostgreSQL; the existing stores are untouched.

```python
db = KernelDB(DbConfig.from_env("query"))        # PG_CORE_URL, PG_JOBS_URL, PG_POOLER, PG_STRICT
await db.start()                                 # pools, capability probe, profile epoch

async with db.uow(write=True) as tx:             # one attempt; the org comes from app.core.org_context
    rows = await tx.fetch("/* pipeshub:docs.list */ SELECT id FROM docs WHERE org_id = $1", org_id)

result = await db.run(fn, write=True)            # re-runs fn on 40001 / 40P01 and a safe connection loss
```

* **No org parameter.** `uow()` reads `org_context.current()`; without it `MissingOrgContext` is raised before a
  connection is acquired. `jobs_uow()` needs a `for_org` or `system` context and a jobs DSN.
* **Prologue** (one statement, `uow.py`): switches to `pipeshub_<service>` and writes `app.org_id`,
  `app.request_id` (sanitised), `statement_timeout`, `lock_timeout`, `plan_cache_mode`, `work_mem` with
  `set_config(..., true)`; write UoWs also call `kernel.assert_profile_epoch`. A role other than `none` before the
  switch is a session `SET ROLE` left by an earlier client: `SessionRoleLeak`, the connection is closed. The login
  parses this statement, so write UoWs need the login to hold USAGE on `kernel` and EXECUTE on the fence.
* **No session state, no client reset.** `PooledConnection` sends no reset query. The pooler must run the targeted
  reset (`server_reset_query`, `server_reset_query_always = 1`; the one the tests use is in `backend/db/tests/test_integration.py`). `pg.pooler=azure_pgbouncer` (no prepared-statement
  tracking) turns the asyncpg statement cache off.
* **A unit fails or commits, never half.** A statement error that the body catches leaves the transaction aborted; the unit
  then raises `FailedTransaction` instead of reporting success. Recover inside a unit only with
  `async with tx.savepoint("name"):` (the error is re-raised after `ROLLBACK TO`). Raw `SAVEPOINT`/`ROLLBACK`/`COMMIT` and
  statements that re-bind `app.*`, `role` or `session_authorization` are refused (runtime `UowUsageError`, and the lint).
* **The server is asked before COMMIT.** Write units run `SELECT now()` right before COMMIT; an aborted transaction
  (`InFailedSQLTransactionError`) raises `FailedTransaction` and rolls back, whatever path caused it (COPY, cancellation,
  anything the flag misses). A unit whose task was cancelled inside it also fails. `BEGIN`/`COMMIT`/`ROLLBACK`/`END`/
  `ABORT`/`SAVEPOINT`/`RELEASE`/`PREPARE TRANSACTION` are refused at runtime (`UowUsageError`), not only by the lint.
* **Protocol-level check.** After every statement the connection must still report an open transaction. A statement
  that ended it (an early COMMIT or ROLLBACK that slipped past the text guard through E-strings, nested comments or quoted
  identifiers) raises `UowUsageError`, fails the unit and discards the connection. An early COMMIT cannot be undone once it
  happened; it is detected immediately and the unit fails loudly. (`COMMIT; BEGIN` in one statement string does not show in
  the connection status; a write unit still catches it, because the `now()` asked before COMMIT differs from the one the unit
  started with, and the text guard refuses the plain forms.)
* **No nesting.** A unit started inside an open one (same task or a child task) raises `UowUsageError` at once; pass `tx`.
  An async generator that holds a unit must be consumed under the org that created it.
* **Prepared statements:** `statement_cache_size` is 0 for every pooler profile; only `PG_POOLER=none` (direct) caches.
* **Errors** (`errors.py`): `SerializationConflict`, `DeadlockDetected` (retried), `DbTimeout`/`LockTimeout`/`PoolTimeout`
  (not retried), `ConnectionLost(retry_safe)`, `CommitOutcomeUnknown`, `SessionRoleLeak`, `ProfileEpochMismatch`,
  `PrologueFailed`, `CapabilityError`. Other asyncpg errors pass through unchanged.
* **Timeouts by class** (`config.py`): query 5 s, api 15 s, indexing 60 s, jobs 15 min; an `org_limits` hook
  (`KernelDB(cfg, org_limits=async fn(org) -> OrgLimits | None)`) can only lower them.
* **Capabilities** (`capabilities.py`, `backend/db/capabilities.yaml`): floors and tested-up-to versions as data.
* **Catalog and lints** (`catalog.py`, `sql_lint.py`): tag every statement `/* pipeshub:<module>.<fn> */`; record with
  `catalog.recording()`, export JSON, and run `explain_gate` in tests; `sql_lint.scan_paths` flags session SQL,
  string-built SQL and untagged statements in modules that use the runtime; `org_predicate_violations` (needs
  `sqlglot`, a dev dependency) requires `<alias>.org_id = $n` on partitioned tables.

Tests: `tests/unit/core/db` (pure) and `tests/integration/db` (real PostgreSQL + PgBouncer from local binaries; see
the module docstring for the environment variables).
