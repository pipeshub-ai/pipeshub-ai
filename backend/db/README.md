# backend/db: PostgreSQL schema migrations

One Alembic environment for the whole product, SQL-first (no models, no autogenerate), on sync psycopg 3.
It runs in its own image (`backend/db/Dockerfile`, `pipeshubai/pipeshub-migrate`) with `python -m db.migrate`.

## Layout
- `versions/<module>/NNNN_<verb>_<object>.{py,sql}`: one directory per module = Alembic branch. Branches normally own their matching PostgreSQL schema; `kernel_0005` is the exception because it creates `platform.consumer_inbox` before the `platform` branch begins.
  The `.py` file is a short wrapper (`run_sql(__file__)`); the `.sql` file is the reviewed artifact.
- Revision ids are `<module>_<NNNN>`; `NNNN` is the module's **epoch**. `env.py` writes it to
  `kernel.schema_epoch` in the same transaction as the revision. On a database that already has revisions, one transaction covers
  all the pending revisions and the catalog and surface checks below, so a run that exits 4 rolls back the revisions and the epochs it
  applied and leaves the database as it found it. An empty database is built revision by revision and checked at the end (all of them in
  one transaction hold about 6,600 locks, more than a default `max_locks_per_transaction` is sized for); if an upgrade of an installed
  database ever fails with "out of shared memory", raise `max_locks_per_transaction` and re-run: the failed run changed nothing. A revision that sets `min_code_epoch = N` is a
  contract: code built before epoch N must not run (`boot_assert.schema_problems`).
- `kernel_0001`: roles (only when missing), schemas `kernel` and `ext`, `schema_epoch`/`set_epoch`,
  `completed_jobs`/`require_job_done`, `pg_major`, `has_extension`. `kernel_0002`: `platform.deployment_profile`
  and `kernel.assert_profile_epoch`. `kernel_0003`: the org isolation registry and helpers (see Org isolation). `kernel_0004`:
  the `kernel.rls_violations` catalog check. `kernel_0009`: registry columns, the vetted function checks (`kernel.vetted_violations`, folded into `kernel.rls_violations`). `kernel_0010`: `kernel.org_lineage` and its extension point (see Vetted functions). `kernel_0011`: person-level tables and functions, jobs-only import/sweep checks, the dedupe exception, the fence barrier, lock keys and sequence waivers (see Person-level tables and jobs functions).
- `platform` (schema = branch) starts empty at `platform_0001` and depends on the kernel. `platform_0001` adds `platform.store_epoch` (one row naming the live
  document store, its epoch and a read-only flag), `kernel.assert_store_epoch(int)`, the registered prologue fence that the Node write units call, and
  `kernel.postgres_store_epoch()`, the start-up reader (both SECURITY DEFINER, owned by `pipeshub_vetted_kernel`; the table is in `kernel.vetted_extension_reads`
  and the service roles hold no privilege on it). Other modules add their own branches and tables with the code that uses them.
- `platform_0002`: the state of background jobs. `platform.job_start`, `platform.job_finish` (executable by the owner role, for the operator) and
  `platform.job_checkpoint` (the jobs role; progress only, only while the job runs) are the only writers of `platform.jobs`; owner `pipeshub_vetted_platform`.
  A unique partial index admits one running job of a kind.
- `platform_0003`: the runtime's own state when no Redis is deployed (`KV_STORE_TYPE=postgres`, `MESSAGE_BROKER=postgres`). `platform.kv_entries` (key, bytes, optional expiry) is the key-value store; `platform.queue_messages`, `queue_groups` and `queue_deliveries` are a work queue with consumer groups (claim with `FOR UPDATE SKIP LOCKED`, attempts, retry backoff, dead letter, NOTIFY wake-up; an ephemeral group is dropped when its process stops refreshing it). All four are `internal` tables (no org column: an event's org travels in its signed envelope), granted to the four service roles. Reached through `KernelDB.runtime_uow()` (Python) and `withUow({ preOrg: true })` (Node).
- `kernel_0014` (the key-value role): `pipeshub_kv`, a service role that reads and writes `platform.kv_entries` and calls the fence and holds nothing on any org table, for the processes that only read configuration (`PG_CORE_KV_URL`).
- `platform_0004`: the job scheduler without Redis. `platform.sched_jobs` (one row per run: waiting/delayed/active/completed/failed, `run_at`, attempts, progress, claim) and `platform.sched_repeats` (one row per schedule). Internal tables like the queue tables; claimed with `FOR UPDATE SKIP LOCKED`, so two Node processes never start the same run. Used by `PgJobQueue` / `PgJobWorker` (`libs/services/pg-job-queue.service.ts`) when `CRAWL_SCHEDULER=postgres`.
- Extra module directories: `EXTRA_MIGRATION_LOCATIONS` in `backend/python/app/edition.py` (paths relative to
  `backend/`). Empty here.

## Running
`PG_MIGRATE_URL=postgresql+psycopg://pipeshub_migrate:...@host:5432/db python -m db.migrate` (from `backend/`).
Direct PostgreSQL address only: the runner takes a session advisory lock, then checks in pg_locks that the
session really holds it, and refuses (exit 2) when it does not, which is what happens behind a pooler.
Exit codes: 0 done, 1 a migration failed, 2 configuration, 3 lock wait timed out
(`PG_MIGRATE_LOCK_TIMEOUT_S`, default 600), 4 `kernel.rls_violations` not empty. Downgrade is development
only (`PG_MIGRATE_ALLOW_DOWNGRADE=1 python -m db.migrate downgrade <rev>`).

## Writing a revision
Expand then contract; never rename; backfills are jobs, not revisions. Index builds on existing tables go
through `lib/ddl.py` (`ensure_index`, `ensure_partitioned_index`), partitions through `add_partition`.
**Rollback safety.** After `helm rollback`, an older build accepts a newer schema whenever every `min_code_epoch` is within its code epoch, so a revision must say whether older code still works. `python -m db.lint` fails a revision that does any of the following unless its `.py` sets `min_code_epoch = N` (an integer above 0: older code must not run) or a module-level `older_code_compatible = "<why older code still works>"` (at least four words). A comment or `min_code_epoch = 0` does not count. Statements are matched after comments and whitespace are normalised, so layout does not matter; the SQL checked is the `.sql` file (statements inside `DO` bodies included, `IF ... THEN` and `PERFORM` stripped) plus, from `upgrade()` and every module function it calls (not `downgrade()`), the string constants passed to `execute`, `exec_driver_sql` (also through an alias or `getattr`), `text` and `ensure_index`, and Alembic's schema calls (`create_check_constraint`, `create_foreign_key`, `create_unique_constraint`, `create_primary_key`, `drop_constraint`, `alter_column`, `add_column`, `drop_column`, `drop_table`, `rename_table`, `create_index`, `drop_index`), which map to the matching SQL rule. An argument built at run time needs the mark or waiver. DROP / RENAME / SET SCHEMA / ALTER ... TYPE / DROP VIEW (and TYPE, SEQUENCE, DOMAIN, ROLE, OWNED, EXTENSION) / TRUNCATE / ALTER ROLE ... NOLOGIN need the epoch mark (a view dropped and re-created in the same file is exempt); a waiver does not clear them. The rest accept either, and apply to a table or function the file did not create (a `CREATE TABLE IF NOT EXISTS` of a table any module's revision creates counts as existing):
- `ADD COLUMN ... NOT NULL` without a default (`DEFAULT NULL` is none), `SET NOT NULL` or `DROP DEFAULT` (older inserts fail);
- a new CHECK, FOREIGN KEY, UNIQUE, PRIMARY KEY or EXCLUDE constraint, `NOT VALID` included (it still checks new writes), or a column added with an inline CHECK / REFERENCES; `ALTER DOMAIN`;
- tightened row-level security: `CREATE` / `ALTER` / `DROP POLICY`, `ENABLE` / `FORCE ROW LEVEL SECURITY`, or `kernel.apply_rls` (positional or `p_rel =>`);
- `CREATE OR REPLACE` / `DROP` / `ALTER` of a FUNCTION or PROCEDURE, `CREATE OR REPLACE VIEW` / `ALTER VIEW`, and `CREATE` / `DROP TRIGGER`. A vetted function is changed by re-running its generated file, so the check follows the revisions that name it in `run_vetted` (any one with the mark or waiver passes);
- a unique index, in SQL or built in the `.py` with `ensure_index` / `ensure_partitioned_index(..., unique=True)`;
- `REVOKE` from any role other than `PUBLIC`, except the two generated housekeeping forms (`REVOKE CREATE ON SCHEMA s FROM pipeshub_vetted_*` and `REVOKE pipeshub_vetted_* FROM pipeshub_owner`); a table or function privilege taken from a service, app or vetted role can break older code;
- `DROP CONSTRAINT` (a dropped unique or primary key breaks `ON CONFLICT`), `ALTER TABLE ... DETACH PARTITION`, `CREATE RULE`;
- a `DO` block with `EXECUTE` (dynamic SQL cannot be parsed).
Review item for every revision: run the previous release's code against the new schema in your head (inserts, updates, reads, function calls) and write the answer into the waiver. A development downgrade (`PG_MIGRATE_ALLOW_DOWNGRADE`) keeps the contract: below the contract revision the branch row reads, for example, epoch 1 with `min_code_epoch` 11, and every build refuses that database until it is upgraded again. Not parsed: function names passed to `run_vetted` through a variable, SQL that reaches a database through an object other than those above, and the waiver's truth.

CI: `python -m db.epoch_lint`, `python -m db.lint`; `python -m db.schema_requirements kernel,platform > schema_requirements.json`
writes a build artifact's requirements, checked at boot by `boot_assert.py` (Python) and `nodejs/apps/src/libs/db/schema-assert.ts` (Node).

## Org isolation (row-level security)
Every table that holds org data has `org_id text NOT NULL` as the leading key column and is registered in the same
revision that creates it:

    SELECT kernel.apply_rls('app.docs', 'org', p_jobs => true, p_vetted => true);   -- the whole tree, owner/migrate only
    -- class 'org': own org; 'lineage': also reads the orgs in app.org_lineage; 'global': no RLS and no app privileges,
    -- reached only through vetted functions

Policies read `app.org_id` through `NULLIF(current_setting('app.org_id', true), '')`, so an unset or empty value matches
nothing. Row-level security is enabled and forced on the table and on every partition. A new partition is made
with `lib.ddl.add_partition` (standalone leaf, `kernel.apply_rls_partition`, then ATTACH). A key that must be unique across
orgs is declared with `kernel.exempt_constraint` and has no app privileges: only its vetted functions touch it.
SECURITY DEFINER functions are classified with `kernel.register_function(proc, 'read'|'write'|'jobs'|'prologue', writer, scope, content_ok)`
and are written as manifest entries (next section). The bootstrap leaves the owner with ADMIN only on each vetted role.

## Vetted functions
A vetted function is a `SECURITY DEFINER` function owned by its schema's `pipeshub_vetted_<schema>` role (one role per schema, no members, no
privilege outside the schema, only on tables that opted in with `p_vetted`/`p_vetted_write`). The kernel schema has its own, created by the
bootstrap. Why: RLS hides indexes behind operators that are not leakproof; the function applies the org predicate itself, so the planner
may use the index, and everything it can touch is bounded by its owner.

Write the function as an entry in `<module>/vetted/manifest.yaml` (format and the template: `lib/vetted.py`). Every field is checked against a strict schema: identifiers match `^[a-z_][a-z0-9_]*$`, types and `returns` come from a short list, `default` and `no_org` are a number, NULL, true/false or a plain quoted string, flags are booleans, and the body may RAISE only a constant EXCEPTION with an SQLSTATE; the generator quotes identifiers as `quote_ident` does. Generate the SQL with:

    python -m db.lib.vetted render versions/<module>/vetted/manifest.yaml     # writes versions/<module>/vetted/<name>.sql
    python -m db.lib.vetted check  versions/<module>/vetted/manifest.yaml     # CI: the checked-in SQL is what the manifest says

A revision installs it with `run_vetted(__file__, "<name>")`. The generated SQL validates every argument (SQLSTATE `22023`), takes the org only
from `app.org_id` (no org: no rows), pins `search_path = pg_catalog, <schema>[, ext], pg_temp` (**pg_temp last**), creates the function as the
vetted role (the owner borrows it for that transaction: SET and CREATE on the schema, then released), revokes PUBLIC, grants EXECUTE and
registers the function. The review checklist, and where it is enforced:

| Checklist item | Enforced by |
|---|---|
| 1 read-only, STABLE, no DML, no dynamic SQL (`EXECUTE`, `format()`, `query_to_xml`), no `ON CONFLICT` in a write function, SQL or PL/pgSQL only; no side channel: `RAISE` only as a constant `EXCEPTION` with an SQLSTATE (no notice, no format argument, no DETAIL, no `ASSERT`) | checks 36, 55, 56, 57, 74; the suite fails on any notice |
| 2 path is `pg_catalog`, own schema, `ext`, `pg_temp` last | checks 16, 58; the shadowing test |
| 3 org from the GUC, never an argument; global lookups are exact-match | checks 59, 60, 61; foreign-org test |
| 4 every argument validated, `22023` | check 62; validation test |
| 5 minimal columns: no whole rows (also as `json`), no content column unless registered `content_ok` | checks 63, 64 |
| 6 hard LIMIT | check 65 |
| 7 owner, no PUBLIC, only the application (never jobs, backup, restore) | checks 15, 17, 66; privileges test |
| 8 reads only its schema's tables that opted into `vetted_read` | checks 42, 73; manifest `reads` check |
| 9 tests exist | every registered function is in a manifest and the manifest agrees with the registry (`71`, run by `db.migrate`) |
| 10 the plans: no sequential scan of an org-scoped table above 1,000 rows on any of seven calls, and the named index (whole name) on calls one and seven | plan gate (`vetted_harness.check_plans`) |
| 11 nothing that raises on row data (casts, division, date parsing, jsonb paths) | check 67 |

A manifest entry's `test:` section feeds the generated suite: `needle` (SQL that inserts the row only org B owns; `{org}` is the needle org),
`args` (sample arguments), `own_result` / `other_result` / `no_org_result` (exact rows for the owning org, another org, no org; the default
for the last two is no rows), `state` (a query whose answer must not change when another org calls a write function), `bad_args` (each must
fail with `22023`), `index` and `volume` (SQL that fills the table for the index and timing checks), `max_ms`, or `checks: [...]` to list the
only checks that apply (a helper has `privileges` and `catalog`).

The surface snapshot lists each function's owner, flags, settings and a hash of its body, so any change to a body is a reviewed diff.
Per function the manifest drives a generated suite (`tests/test_vetted.py`): no org, a foreign org, the own org, index use, validation,
privileges, catalog, timing and shadowing by a caller's temp table.

**Plan gate.** `vetted_harness.check_plans` fails on a sequential scan of an org-scoped table with more than 1,000 estimated rows (tables are analysed first) and on a missing index, matched as a whole name. `vetted_harness.plans_auto_explain` loads `auto_explain` in an administrator session (LOAD needs superuser), switches to the
service role, calls the function seven times and reads the nested plans from the notices (the plpgsql plan cache goes generic after five
calls). Where LOAD is not allowed (managed platforms), `plan_explain` switches to the vetted role and EXPLAINs the function's query with the
org set; `check_index` falls back to it by itself.

**`kernel.org_lineage(org)`** is the list the unit of work puts in `app.org_lineage`: the caller's org first, then the parents returned by
`kernel.org_parent_ids(org)` (sorted, at most 16, well-formed ids only). Asking for another org's lineage answers like having no org. By default `org_parent_ids` returns nothing, so the lineage is the org alone. `org_parent_ids` is an extension point: its signature is
frozen (check 68), nobody but `org_lineage` can execute it (check 72), and an extra migration location replaces only its body (only functions named in `EXTENSION_POINTS` in `lib/vetted.py` may be extended) with a
manifest entry marked `extends: true` (same signature, class and scope) and an `allowlist.yaml` whose `surface:` lists the new function
line and whose `remove:` lists the old one. A body that reads the location's own tables first claims a new, empty schema
(`kernel.declare_extension_schema`) and then calls `kernel.grant_vetted_owner_select(table)` for tables in it (SELECT and USAGE only, nothing in
any other schema; the grant is a snapshot line the allowlist must list).

Two layers decide whether a database is acceptable, and both must be empty (the migrate job exits 4 otherwise, CI runs both on an empty
database, and the doctor command should too):

1. **The surface snapshot, deny by default** (`surface.py`, expected text in `surface.snapshot.txt` and any `surface*.txt` next to a
   module, plus an `allowlist.yaml` in each directory of `EXTRA_MIGRATION_LOCATIONS` (a `surface:` key, one `- "<line>"` item per line, merged into
   the expected state; a `remove:` key drops the base lines the location replaces; the OSS default has none), plus files named in `PG_MIGRATE_SURFACE_FILES`). One sorted line per fact about the security surface: roles and
   attributes, every membership edge with its ADMIN/INHERIT/SET options, role and database settings, ACLs on databases, schemas,
   relations, columns, functions, types, languages, large objects and foreign servers, default privileges, policies, RLS and FORCE
   flags, rules, triggers, constraints, indexes, view and function definitions (hashed), SECURITY DEFINER / LEAKPROOF flags and function
   settings, operators and casts, built-in function attributes (name, arguments, language, owner, SECURITY DEFINER, LEAKPROOF, volatility, kind, settings; not the OID, body or library path, which differ between builds of one major; one hash per PostgreSQL major, `sysfuncs@17 ...`, and a line for any built-in that is SECURITY DEFINER or has settings), extensions, foreign data, publications, subscriptions and event triggers. A live line that is not
   expected, or an expected line that is not live, is a violation (rule `50`). A change to any of it shows up as a diff in the pull
   request that makes it: regenerate with `UPDATE_SURFACE=1 pytest` (or `python -m db.surface` on a migrated database) and review.
   `PG_MIGRATE_SURFACE_CHECK=skip` switches the comparison off (development only). It is skipped while a restore window is open.
2. **Semantic checks** (`kernel.rls_violations`, checks 01-51), which say why: every relation outside schema `kernel` is registered
   (org, lineage, global or internal), keys and foreign keys lead with `org_id`, policies match their templates, `pg_temp` is
   last, function classes and who may execute them, membership allowlist, administrator separation, column grants, rules, views,
   foreign data, sequences, default privileges, publications and event triggers, LEAKPROOF, large objects, the consumer inbox key,
   and invalid indexes. Every role in the cluster is checked, whatever its name. Roles that own objects, the owner, the group, the
   service roles and the vetted roles never log in. Each service role has exactly one member, its own `<role>_login`, without ADMIN.
   `kernel.settings.admins` is an explicit reviewed list of human administrators: they may hold administrator roles, but may not be
   members of or hold any application role, no application role may reach them, and they may not be listed if they are ours. No role
   has BYPASSRLS, and the only superusers are the bootstrap superuser and the database owner. `kernel.settings.extensions` lists the
   extensions that may be installed; only their members are exempt from the object checks.

Privileges are attributed through the whole membership graph (inherit or SET), so a privilege parked on a helper role counts for
every role that reaches it.

Roles: `python -m db.bootstrap_roles [--vetted schema,schema]` (administrative `PG_ADMIN_URL`; idempotent, additive, no
role gets SUPERUSER, CREATEROLE or BYPASSRLS). On an existing database, run it again before every `db.migrate` upgrade: a new revision can need a role the old bootstrap did not create, and migrate stops at that revision. Each service has a login `pipeshub_<svc>_login`, a member of its own NOLOGIN
service role `WITH INHERIT FALSE, SET TRUE`: the login holds no privileges until the unit of work switches role. No role
bypasses RLS: `pipeshub_backup` reads every org through a SELECT policy, `pipeshub_restore` (NOLOGIN) inserts through a
policy that admits only the org of an open restore window.

Restore: `python -m db.restore_window open --org <id> [--hours N]` (needs `PG_ADMIN_URL`, `PG_MIGRATE_URL`, `PG_BACKUP_URL`,
`PG_RESTORE_PASSWORD`) switches the application logins off and disconnects them, refuses unless that org has no rows,
and gives `pipeshub_restore` a login that expires by itself. Export the org with `export --org <id> --dir <d>` (only that org's rows, parents first), `load` it as `pipeshub_restore`
(a file holding another org's row is refused before anything is inserted: the database rejects a whole multi-row INSERT if
one row is foreign, so a mixed dump would lose rows), then
`sequences` (the migrator sets every column-owned sequence from the restored data), then `close`. Global tables are not
restorable by this role; they load through vetted import functions. `load` is one transaction for every table and takes table names only from
the registry (it refuses anything else). If `open` is interrupted, `close --abandon` brings the logins back. PgBouncer should set `server_login_retry` low so
the logins come back quickly (2 works well).

`kernel_0005` adds `platform.consumer_inbox` (primary key `(org_id, message_id)`; check 44) for consumer replay protection.
Server-side large objects have no org, so the bootstrap revokes their functions from PUBLIC (check 43). The PgBouncer reset
includes `DEALLOCATE ALL` (with `max_prepared_statements` on, PgBouncer keeps protocol-level statements working), so SQL
`PREPARE` text does not reach the next client.

On a managed platform that cannot revoke the large-object functions, set `{"managed": true}` in `platform.deployment_profile.profile`: check 43's
function rows become warnings (`kernel.rls_warnings`) and that one snapshot line is not compared.

## Person-level tables and jobs functions
Some identity tables are keyed by a person, not by an org (a credential, a sign-in code). They are `global` tables with no `org_id`
(`kernel.apply_rls(rel, 'global', p_vetted_write => true)`), marked with `kernel.mark_person(rel)` (check 76: no `org_id` column).
The application holds no privilege on them; only vetted functions of scope `person` reach them (manifest keys in `lib/vetted.py`):

| Effect | Rule (generated template; checks) |
|---|---|
| `read` | Before an org is chosen (`app.org_id` empty) an exact email is answered. With an org set, only the acting person's own email (`actor` resolves it from `app.org_id` and `app.user_id`); any other email, a stranger and a missing principal get the same empty answer (78, 79). No caller-chosen limit or threshold, and a constant `LIMIT` (85). |
| `self` | The acting principal's email must equal the person's; then the per-email lock, then the membership proof (`member`) (78-81). |
| `admin` | One of a closed list (`unblock`, `role_change`, `admin_signout`, `clear_if_last`): the principal must be an admin of the org (`admin_check`), the person a live member, and an audit row is written (82, 83). |
| `preorg` | Only while no org is set; returns nothing except `consume_otp` (84). |

Every person write takes `pg_advisory_xact_lock(7350, hashtext(email))` before it reads the membership proof (80). Advisory locks are
transaction-scoped, two-integer, and the first integer is a class in `kernel.lock_keys` (93). A person write may also write other
orgs' org-scoped rows of the person (`fanout`, tables with `vetted_write`) because it holds the proof; no other function may (`db.lib.vetted.problems`).

**Jobs-only functions.** A `jobs`-class function is named `import_*` or `sweep_*`, executable by the jobs role only (31: by registry class, by the
owner of the global table it writes, and by behaviour). An import names `requires_job` kinds and refuses with `55000` unless
`platform.jobs` holds a running job of one of them (its schema's vetted owner reads that table through `kernel.grant_job_gate(schema)`). A sweep
takes no arguments and deletes by a fixed `now()` expiry predicate. Deletion is for sweeps alone: `kernel.allow_vetted_delete(rel)` opts one global
table in; no read or write function deletes (88) and no other table grants DELETE to a vetted role (89).

**External-issuer dedupe.** A write function may answer "already processed" (`ON CONFLICT DO NOTHING`, returns boolean) only for a global table without
`org_id` whose key is exempted as `external_issuer`; the manifest names its `callers` and `python -m db.lint` fails on any other source path calling it (90).

**Fence.** A `prologue` function (`kernel.assert_profile_epoch`) is `VOLATILE`, `SECURITY DEFINER` and asserts `READ COMMITTED`. It takes the shared
transaction-scoped barrier lock (class in `kernel.lock_keys`) held to commit and refuses with `25006` when a switch holds it exclusively; the switch
takes the same key exclusively in the transaction that changes the epoch. The logins hold `USAGE` and `EXECUTE` only; no application role reads the table the fence reads (32).
Both runtimes reference the fence's output column in the prologue's outer `SELECT`, and read units are `READ ONLY`.

**Shared sequences.** A sequence may feed an internal surrogate. A text column whose default calls `nextval()` shows the value, so it needs a
reviewed `kernel.waive_sequence(...)` row from a migration (91, 92). A caller-supplied unique value (an org slug) is an exempted `public_by_design` key; its vetted write
answers the same for a taken and a malformed value and has no `ON CONFLICT` (57).

## Tests
`cd backend/db && pytest`. Integration tests start throwaway clusters from `MIGRATE_TEST_PG_BINS` (comma-separated
PostgreSQL `bin` dirs; skipped when unset and no `initdb` on PATH). Needs `pip install -r requirements.txt pytest`.

## If a runner dies
The lock belongs to the runner's session and is released when PostgreSQL notices the client is gone (TCP
keepalives, about a minute at worst; at once on a process kill). The migration session sets
`client_connection_check_interval=5s`, so a statement still running for a dead runner is cancelled within
seconds, and `idle_in_transaction_session_timeout=10min` ends an abandoned open transaction; `statement_timeout`
is 15 min. Each revision is one transaction, so nothing is half applied: run the job again. If a re-run reports
lock timeouts for longer than that, find the old backend (`SELECT pid, state, query FROM pg_stat_activity
WHERE usename = 'pipeshub_migrate'`) and `pg_terminate_backend` it.

The org isolation tests run through a real PgBouncer (transaction pooling, a pool of two server connections, the kernel reset
settings): fault injection per registered table (LIST/HASH and time-RANGE trees) and 3,000+ pooled transactions with no
cross-org row.

CI: `.github/workflows/db-migrations.yml` (lints, an empty database migrated to heads with the catalog check, and the tests on PostgreSQL 17 and 18 service containers,
`MIGRATE_TEST_PG_EXTERNAL=host:port`; `MIGRATE_TEST_PGBOUNCER=<binary>` enables the real-pooler test).
