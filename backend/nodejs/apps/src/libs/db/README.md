# libs/db: PostgreSQL runtime for the Node API

Opt-in. Nothing in the API imports this module until a feature selects PostgreSQL; `withUow` throws
`UowUsageError` ("not configured") until the process calls `configurePg(new PgRuntime({...}))`.

```ts
const pool = createPgPool({ connectionString, service: 'node', version });
configurePg(
  new PgRuntime({
    pool,
    defaultService: 'node',
    fence: { fn: 'kernel.assert_store_epoch', epoch },
  }),
);

await withUow({ write: true }, async (tx) => {
  const { rows } = await tx.query(
    '/* pipeshub:identity.user_by_id */ SELECT ... WHERE org_id = $1 AND id = $2',
    [tx.orgId, id],
  );
});
```

## What a unit of work does

1. Reads the org from `OrgContext` (`libs/context/orgContext.ts`). No context means `MissingOrgContext` before a connection
   is taken. A `system` context and the bootstrap org literal are refused. Never take the org from a request body or header.
   Lookups that happen before the org is known pass `preOrg: true` (explicit, `app.org_id = ''`).
2. `BEGIN` (`READ ONLY` unless `write: true`), then **one prologue statement** that: reads the current role (the guard),
   sets `role`, `app.org_id`, `app.request_id` (sanitised), `app.user_id`, `statement_timeout`, `lock_timeout`,
   `plan_cache_mode`, `work_mem` with transaction scope, and for write units calls the configured fence function.
   The login needs `USAGE` on the fence's schema and `EXECUTE` on it, because the statement is checked before the role switch.
3. Guard: a role other than `none` before the switch means a session role leaked through the pooler. The unit rolls back,
   destroys the connection, counts `pipeshub_db_session_role_leak_total` and throws `SessionRoleLeak`.
4. Runs the callable, `COMMIT`.

`withJobsUow` is the same for job processes (jobs pool, role `pipeshub_jobs`, a `system` context runs with no org).
A unit started inside a unit is refused with `UowUsageError`, whatever the org: pass `tx` down.
`tx.savepoint(name, fn)` is the only way to use savepoints (raw `SAVEPOINT`/`ROLLBACK TO` are refused by the lint): it rolls back to the savepoint and rethrows, so the unit can handle a duplicate key and continue.
A `COMMIT` that answers `ROLLBACK` (an error was swallowed and no later statement ran) throws `FailedTransaction`.
A statement that ends the transaction behind the text guard (a `COMMIT` hidden by quoting the guard misreads) is caught two ways, and either throws `FailedTransaction`, rolls back and discards the connection: after every statement the client's transaction status must still be "in a transaction", and a write unit confirms before `COMMIT` (one extra round trip, about 0.06 ms through PgBouncer on loopback) that `now()` is still the prologue's value, which catches `COMMIT; BEGIN` in one string. Work the server ran before the early `COMMIT` stays committed.
Event listeners that outlive a request must capture the org (from the event payload) and start work inside `spawnDetached` plus an explicit context; a listener fired from another request's context would run as that request's org.

## Statements

- Unnamed by default, so every execution is planned with its parameters and no pooler setting is needed.
  A named statement is accepted only in a `customPlan: true` unit and not when `pooler: 'azure_pgbouncer'`.
- Every statement is checked by `lintSql`: no session `SET`/`RESET`, `set_config(..., false)`, cursors, `LISTEN`, SQL
  `PREPARE`, `DISCARD`, session advisory locks, `CREATE TEMP` without `ON COMMIT DROP`, or transaction control.
- Statements carry a constant tag `/* pipeshub:<module>.<fn> */`. Attach a `QueryCatalog` to export them (tag, SQL, sample
  args) for the planner gate. `scanSource` finds untagged, interpolated or banned SQL in source files.
- `tx.db<DB>(tag)` returns a Kysely builder bound to the transaction (same guards, tag added). Kysely cannot begin or commit.
- Advisory locks: `registerLockKey(name, classId)` then `tx.advisoryXactLock(key, objectId)`; only transaction-scoped forms.

## Retries and errors

- 40001 and 40P01: the whole callable re-runs, up to 3 retries, jittered backoff from 10 ms.
- A pool timeout is retried once for read units and `idempotent` units.
- Lost connection: retried once, only before the first statement or for an `idempotent` unit, never after `COMMIT` was sent
  (`ConnectionLost.commitSent`).
- Timeouts (57014, 55P03) are not retried: `DbTimeout`, `LockNotAvailable`.
- Typed errors in `errors.ts`: 23505 `UniqueConstraintError`, 23503 `ForeignKeyError`, 25006 `DbReadOnly`, 55P04/55000
  `DbFenced`, 25P02 `FailedTransaction`, 42501 `PermissionDenied`, plus `PrologueFailed`, `PoolTimeout`. The repository seam's `VersionConflictError` (libs/repositories/errors) is what a guarded update raises.
- Write units run at read committed only (`WriteIsolationRefused` otherwise); the write fence asserts it server-side too. Higher levels are for read-only units.

## Store fence

`platform.store_epoch` (one row: `backend`, `epoch`, `readonly`) says which store holds the application's documents. A write unit's prologue calls
`kernel.assert_store_epoch(epoch)` (`backend/db/versions/platform/vetted/assert_store_epoch.sql`; SECURITY DEFINER in schema `kernel`, owned by the kernel's vetted role, so the service roles hold no privilege on the table): it asserts READ COMMITTED (a REPEATABLE READ writer would pass
on its old snapshot), takes the **shared** transaction-scoped advisory lock `(7340, 1)` (registered in `kernel.lock_keys` as `docstore_fence`; the Node lock-key registry refuses the class, held to commit; a failed try is `25006`),
then refuses a row that is not `postgres` at this process's epoch (`55P04` -> `DbFenced`) or is `readonly` (`25006` -> `DbReadOnly`). A freeze is
`BEGIN; SET LOCAL lock_timeout; SELECT pg_advisory_xact_lock(7340, 1); UPDATE platform.store_epoch SET readonly = true; COMMIT`: the exclusive lock is granted
only after every writer in flight has committed, and every later writer is refused, so no write commits after the freeze's commit
(`tests/libs/db/store-fence.itest.ts`, commit timestamps). Read units never call it. Pass `fence: { fn: 'kernel.assert_store_epoch', epoch }` to `PgRuntime`
(`initDocStore` in `libs/repositories/doc-store.ts` does, with the epoch from `readStoreEpoch`).

## Boot

`waitForSchema(rt, requirements)` reads `kernel.schema_epoch` as the service role and applies `schemaProblems`
(`schema-assert.ts`); it throws `SchemaNotReady` after the last attempt and the caller decides to exit.
`readProfileEpoch`, `probeCapabilities` and `evaluateCapabilities` cover the deployment epoch and extension floors.

## Types

`generated/kernel-tables.ts` is generated from the migrated catalog. The integration test compares it with a freshly migrated
database. Regenerate with `PG_TYPES_UPDATE=1` and the integration command below, or with
`PG_TYPEGEN_URL=... node dist/scripts/gen-db-types.js <out.ts> kernel.schema_epoch ...`.

## Tests

- Unit (no database): `npx cross-env TS_NODE_PROJECT=tsconfig.test.json mocha tests/libs/db/*.test.ts`
- Integration (real PostgreSQL 17/18 and PgBouncer with the kernel reset), skipped without the binaries:
  `PG_TEST_BINS=<bin dirs, comma separated> PG_TEST_PGBOUNCER=<pgbouncer> [PG_TEST_MIGRATE_PYTHON=<python with the migration deps>] npm run test:pg`
