/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Client, Pool } from 'pg'
import { Bouncer, Cluster, PG_BINS, createKernelDb, haveStack, startBouncer, startCluster } from './pg-harness'
import {
  DbFenced, DbReadOnly, PgRuntime, WriteIsolationRefused, createPgPool, generateTypes, readStoreEpoch,
  waitForSchema,
} from '../../../src/libs/db'
import { MissingOrgContext, makeOrgContext, runWithOrgContext } from '../../../src/libs/context/orgContext'
import { PgUnitOfWork, assertVersionMatched, kernelTx } from '../../../src/libs/repositories/pg'
import {
  DocStoreFencedError, DocStoreReadOnlyError, UniqueConstraintError, VersionConflictError,
} from '../../../src/libs/repositories/errors'
import { InternalServerError } from '../../../src/libs/errors/http.errors'

const A = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const inOrg = <T>(fn: () => T) =>
  runWithOrgContext(makeOrgContext({ orgId: A, userId: 'u1', kind: 'user', source: 'jwt', requestId: 'r1' }), fn)
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const Q = (tag: string, sql: string) => `/* pipeshub:${tag} */ ${sql}`
const DB = 'sf_app'
// The kernel's registered barrier (kernel.lock_keys: docstore_fence). The Node registry refuses this class, so the tests name it directly.
const FENCE_CLASS = 7340
const FENCE_OBJECT = 1

const suite = haveStack ? describe : describe.skip

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`store fence against PostgreSQL (${bin}) and PgBouncer`, function () {
    this.timeout(120_000)
    let cluster: Cluster
    let bouncer: Bouncer
    const pools: Pool[] = []
    const work = process.env.PG_TEST_WORK ?? tmpdir()

    const runtime = (epoch: number | (() => number) = 1, login = 'pipeshub_node_login') => {
      const pool = createPgPool({ connectionString: cluster.url(DB, login, bouncer.port), service: 'node', version: 'test', max: 10 })
      pools.push(pool)
      return new PgRuntime({ pool, defaultService: 'node', fence: { fn: 'kernel.assert_store_epoch', epoch } })
    }
    const admin = <T>(fn: (c: Client) => Promise<T>) => cluster.admin(fn, DB)
    const setFence = (backend: string, epoch: number, readonly: boolean) =>
      admin((c) => c.query('UPDATE platform.store_epoch SET backend = $1, epoch = $2, readonly = $3', [backend, epoch, readonly]))
    const insert = (rt: PgRuntime, id: string, hold = 0) =>
      inOrg(() => rt.withUow({ write: true }, async (tx) => {
        await tx.query(Q('t.ins', 'INSERT INTO kt.docs (org_id, id, body) VALUES ($1, $2, $3)'), [tx.orgId, id, 'x'])
        if (hold) await sleep(hold)
      }))

    /** What the migrator does at the freeze at the cutover freeze: exclusive barrier and the UPDATE in one transaction. */
    async function freeze(): Promise<Date> {
      const c = new Client({ host: '127.0.0.1', port: cluster.port, user: 'postgres', database: DB })
      await c.connect()
      try {
        await c.query('BEGIN')
        await c.query("SET LOCAL lock_timeout = '20s'")
        await c.query('SELECT pg_advisory_xact_lock($1::int, $2::int)', [FENCE_CLASS, FENCE_OBJECT])
        await c.query('UPDATE platform.store_epoch SET readonly = true')
        await c.query('COMMIT')
        return (await c.query('SELECT pg_xact_commit_timestamp(xmin) AS ts FROM platform.store_epoch')).rows[0].ts
      } finally {
        await c.end()
      }
    }
    const commitTimes = (prefix: string) =>
      admin(async (c) => (await c.query("SELECT id, pg_xact_commit_timestamp(xmin) AS ts FROM kt.docs WHERE id LIKE $1", [`${prefix}%`])).rows as Array<{ id: string; ts: Date }>)

    before(async () => {
      cluster = await startCluster(bin, work)
      await createKernelDb(cluster, DB)
      bouncer = await startBouncer(cluster, DB, work, { reset: true, pool: 4, maxPreparedStatements: 200 })
    })
    beforeEach(async () => {
      await setFence('postgres', 1, false)
      await admin((c) => c.query('DELETE FROM kt.docs'))
    })
    after(async () => {
      await Promise.all(pools.map((p) => p.end().catch(() => {})))
      bouncer?.stop()
      cluster?.stop()
    })

    it('a write unit passes the fence at the current epoch and commits; a read unit never calls it', async () => {
      const rt = runtime()
      await insert(rt, 'ok1')
      expect((await admin((c) => c.query("SELECT count(*)::int AS n FROM kt.docs WHERE id = 'ok1'"))).rows[0].n).to.equal(1)
      await setFence('postgres', 1, true)
      const n = await inOrg(() => rt.withUow({}, async (tx) => (await tx.query<{ n: number }>(Q('t.r', 'SELECT count(*)::int AS n FROM kt.docs'))).rows[0]?.n))
      expect(n).to.equal(1)
    })

    it('a stale epoch, or a store that is not postgres, is DbFenced and nothing is written', async () => {
      const e1 = await insert(runtime(7), 'stale').catch((x) => x)
      expect(e1).to.be.instanceOf(DbFenced)
      await setFence('mongo', 1, false)
      const e2 = await insert(runtime(1), 'wrongstore').catch((x) => x)
      expect(e2).to.be.instanceOf(DbFenced)
      expect((await admin((c) => c.query('SELECT count(*)::int AS n FROM kt.docs'))).rows[0].n).to.equal(0)
    })

    it('the row says read-only: writers get DbReadOnly, readers pass, and writing resumes when it is cleared', async () => {
      const rt = runtime()
      await setFence('postgres', 1, true)
      expect(await insert(rt, 'ro').catch((x) => x)).to.be.instanceOf(DbReadOnly)
      await setFence('postgres', 1, false)
      await insert(rt, 'ro')
    })

    it('the barrier: while another session holds the exclusive key, writers get DbReadOnly and readers pass; after COMMIT writers pass', async () => {
      const rt = runtime()
      const holder = new Client({ host: '127.0.0.1', port: cluster.port, user: 'postgres', database: DB })
      await holder.connect()
      try {
        await holder.query('BEGIN')
        await holder.query('SELECT pg_advisory_xact_lock($1::int, $2::int)', [FENCE_CLASS, FENCE_OBJECT])
        expect(await insert(rt, 'held').catch((x) => x)).to.be.instanceOf(DbReadOnly)
        const n = await inOrg(() => rt.withUow({}, async (tx) => (await tx.query<{ n: number }>(Q('t.r', 'SELECT count(*)::int AS n FROM kt.docs'))).rows[0]?.n))
        expect(n).to.equal(0)
        await holder.query('COMMIT')
        await insert(rt, 'held')
      } finally {
        await holder.end()
      }
    })

    it('a writer that passed the fence finishes first; the freeze waits for it; no write commits after the freeze', async () => {
      const rt = runtime()
      const slow = insert(rt, 'slow', 1500)
      // wait until the slow writer really holds the shared fence lock (a fixed pause is too short on a loaded host)
      let held = false
      for (let i = 0; i < 200 && !held; i++) {
        const r = await admin((c) => c.query(
          "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND classid = $1 AND objid = $2 AND objsubid = 2 AND mode = 'ShareLock' AND granted",
          [FENCE_CLASS, FENCE_OBJECT],
        ))
        held = (r.rowCount ?? 0) > 0
        if (!held) await sleep(25)
      }
      expect(held, 'slow writer holds the shared fence lock').to.equal(true)
      const t0 = Date.now()
      const frozenAt = await freeze()
      const waited = Date.now() - t0
      await slow
      expect(waited).to.be.greaterThan(900) // the exclusive key was granted only after the writer committed
      const [row] = await commitTimes('slow')
      expect(row.ts.getTime()).to.be.at.most(frozenAt.getTime())
      expect(await insert(rt, 'late').catch((x) => x)).to.be.instanceOf(DbReadOnly)
    })

    it('16 concurrent writers across a freeze: the freeze completes, every commit is at or before it, none after', async () => {
      const rt = runtime()
      let frozen = false
      const results: Array<{ id: string; ok: boolean }> = []
      const writer = async (w: number) => {
        for (let i = 0; !frozen || i < 3; i++) {
          const id = `race-${w}-${i}`
          const ok = await insert(rt, id, 20).then(() => true, (e) => { if (!(e instanceof DbReadOnly)) throw e; return false })
          results.push({ id, ok })
          if (!ok) break
        }
      }
      const writers = Array.from({ length: 16 }, (_, w) => writer(w))
      // freeze once the writers are really running (a fixed pause is too short on a loaded host)
      for (let waited = 0; results.filter((r) => r.ok).length < 32 && waited < 30_000; waited += 50) await sleep(50)
      const t0 = Date.now()
      const frozenAt = await freeze()
      frozen = true
      const took = Date.now() - t0
      await Promise.all(writers)
      const times = await commitTimes('race-')
      const committed = new Set(times.map((t) => t.id))
      expect(results.filter((r) => r.ok).every((r) => committed.has(r.id))).to.equal(true)
      expect(results.filter((r) => !r.ok).some((r) => committed.has(r.id))).to.equal(false)
      expect(times.filter((t) => t.ts.getTime() > frozenAt.getTime())).to.deep.equal([])
      expect(results.filter((r) => r.ok).length).to.be.greaterThan(16)
      expect(took).to.be.lessThan(15_000) // the freeze's own lock_timeout is 20 s; this only catches a freeze that starves
    })

    it('REPEATABLE READ cannot pass the fence on an old snapshot: the client refuses, and the server refuses a raw writer', async () => {
      const rt = runtime()
      const refused = await inOrg(() => rt.withUow({ write: true, isolation: 'repeatable_read' }, (tx) => tx.query(Q('t.rr', 'SELECT 1')))).catch((x) => x)
      expect(refused).to.be.instanceOf(WriteIsolationRefused)
      // the review's case, without the client's guard: the snapshot predates the freeze
      const c = new Client({ host: '127.0.0.1', port: bouncer.port, user: 'pipeshub_node_login', database: DB })
      await c.connect()
      try {
        await c.query('BEGIN ISOLATION LEVEL REPEATABLE READ')
        await c.query('SELECT 1') // takes the snapshot, readonly = false in it
        await freeze()
        const e = await c.query('SELECT kernel.assert_store_epoch(1)').catch((x) => x)
        expect(e.code).to.equal('25000')
        await c.query('ROLLBACK')
      } finally {
        await c.end()
      }
    })

    it('the fence function is a VOLATILE definer owned by the kernel owner, not callable by PUBLIC, on the registered lock class; the service roles cannot read the table', async () => {
      const r = await admin(async (c) => (await c.query(
        "SELECT p.provolatile, p.prosecdef, p.prosrc, p.proowner::regrole::text AS owner, p.proconfig, has_function_privilege('public', p.oid, 'EXECUTE') AS pub, " +
        "has_function_privilege('pipeshub_node_login', p.oid, 'EXECUTE') AS node, has_function_privilege('pipeshub_node', 'kernel.postgres_store_epoch()', 'EXECUTE') AS reader, " +
        "has_table_privilege('pipeshub_node', 'platform.store_epoch', 'SELECT') AS node_read, has_table_privilege('pipeshub_jobs', 'platform.store_epoch', 'SELECT') AS jobs_read, " +
        "EXISTS (SELECT 1 FROM kernel.lock_keys WHERE class_id = $1 AND name = 'docstore_fence') AS registered " +
        "FROM pg_proc p WHERE p.oid = 'kernel.assert_store_epoch(int)'::regprocedure", [FENCE_CLASS])).rows[0])
      expect(r.provolatile).to.equal('v')
      expect(r.prosecdef).to.equal(true)
      expect(r.owner).to.equal('pipeshub_vetted_kernel')
      expect(r.proconfig).to.deep.equal(['search_path=pg_catalog, kernel, pg_temp', 'standard_conforming_strings=on'])
      expect(r.pub).to.equal(false)
      expect(r.node).to.equal(true)
      expect(r.reader).to.equal(true)
      expect(r.node_read).to.equal(false)
      expect(r.jobs_read).to.equal(false)
      expect(r.registered).to.equal(true)
      expect(r.prosrc).to.contain(`pg_try_advisory_xact_lock_shared(${FENCE_CLASS}, ${FENCE_OBJECT})`)
    })

    describe('the unit of work behind the repository seam', () => {
      it('maps a closed fence and a stale epoch to the seam errors', async () => {
        const uow = new PgUnitOfWork(runtime())
        await setFence('postgres', 1, true)
        const ro = await inOrg(() => uow.run(async () => 1)).catch((x) => x)
        expect(ro).to.be.instanceOf(DocStoreReadOnlyError)
        expect(ro.statusCode).to.equal(503)
        expect(ro.metadata.retryAfter).to.equal('5')
        await setFence('postgres', 2, false)
        expect(await inOrg(() => uow.run(async () => 1)).catch((x) => x)).to.be.instanceOf(DocStoreFencedError)
        // a read-only run is not stopped by the freeze
        await setFence('postgres', 1, true)
        expect(await inOrg(() => uow.run(async () => 'read', { readOnly: true }))).to.equal('read')
      })

      it('a duplicate key is UniqueConstraintError{key} with the key (the server hides the columns under row-level security, so it is the constraint name) and no values; a swallowed one is a 500, with a savepoint it is handled', async () => {
        const uow = new PgUnitOfWork(runtime())
        await inOrg(() => uow.run((tx) => kernelTx(tx).query(Q('t.d', "INSERT INTO kt.docs (org_id, id, body) VALUES ($1, 'dup', 'secret@example.com')"), [A])))
        const dup = await inOrg(() => uow.run((tx) => kernelTx(tx).query(Q('t.d', "INSERT INTO kt.docs (org_id, id, body) VALUES ($1, 'dup', 'secret@example.com')"), [A]))).catch((x) => x)
        expect(dup).to.be.instanceOf(UniqueConstraintError)
        expect(dup.metadata.key).to.equal('docs_pkey') // the server hides the key columns on a table with row-level security
        expect(JSON.stringify(dup.toJSON())).to.not.contain('secret@example.com')
        const swallowed = await inOrg(() => uow.run(async (tx) => {
          const k = kernelTx(tx)
          await k.query(Q('t.d', "INSERT INTO kt.docs (org_id, id) VALUES ($1, 'dup')"), [A]).catch(() => undefined)
          await k.query(Q('t.d', 'SELECT 1'))
        })).catch((x) => x)
        expect(swallowed).to.be.instanceOf(InternalServerError)
        const handled = await inOrg(() => uow.run(async (tx) => {
          const k = kernelTx(tx)
          await k.savepoint('s1', () => k.query(Q('t.d', "INSERT INTO kt.docs (org_id, id) VALUES ($1, 'dup')"), [A])).catch(() => undefined)
          return (await k.query<{ n: number }>(Q('t.d', 'SELECT count(*)::int AS n FROM kt.docs'))).rows[0]?.n
        }))
        expect(handled).to.equal(1)
      })

      it('a version-guarded update that matches no row is VersionConflictError', async () => {
        const uow = new PgUnitOfWork(runtime())
        await inOrg(() => uow.run((tx) => kernelTx(tx).query(Q('t.v', "INSERT INTO kt.docs (org_id, id, version) VALUES ($1, 'v1', 3)"), [A])))
        const stale = await inOrg(() => uow.run(async (tx) => {
          const r = await kernelTx(tx).query(Q('t.v', "UPDATE kt.docs SET body = 'y', version = version + 1 WHERE org_id = $1 AND id = 'v1' AND version = $2"), [A, 2])
          assertVersionMatched(r.rowCount, 'doc')
        })).catch((x) => x)
        expect(stale).to.be.instanceOf(VersionConflictError)
        await inOrg(() => uow.run(async (tx) => {
          const r = await kernelTx(tx).query(Q('t.v', "UPDATE kt.docs SET body = 'y', version = version + 1 WHERE org_id = $1 AND id = 'v1' AND version = $2"), [A, 3])
          assertVersionMatched(r.rowCount, 'doc')
        }))
      })

      it('needs an org context like any unit', async () => {
        const e = await new PgUnitOfWork(runtime()).run(async () => 1).catch((x) => x)
        expect(e).to.be.instanceOf(MissingOrgContext)
      })
    })

    describe('the document store schema branches', () => {
      it('the store epoch is read through the kernel reader at boot and the empty branches are at epoch 1', async () => {
        const rt = runtime()
        expect(await readStoreEpoch(rt)).to.equal(1)
        const branches = { platform: { requires: 1, code: 1 }, identity: { requires: 1, code: 1 }, conversation: { requires: 1, code: 1 }, content: { requires: 1, code: 1 }, oauth: { requires: 1, code: 4 }, integrations: { requires: 1, code: 1 }, platform_data: { requires: 1, code: 1 } }
        await waitForSchema(rt, branches)
        await setFence('mongo', 1, false)
        expect(await readStoreEpoch(rt).catch((x) => x)).to.have.property('problems')
      })

      it('the catalog check is clean and the schemas hold the fence and the conversation tables', async () => {
        const v = await admin(async (c) => (await c.query('SELECT rule, object FROM kernel.rls_violations')).rows)
        expect(v).to.deep.equal([])
        const t = await admin(async (c) => (await c.query(DOCSTORE_TABLES_SQL)).rows.map((r) => `${r.nspname}.${r.relname}`))
        expect(t).to.deep.equal([
          'content.documents', 'content.project_members', 'content.projects',
          'conversation.chat_messages', 'conversation.chat_session_members', 'conversation.chat_sessions', 'conversation.citations',
          'conversation.semantic_search_members', 'conversation.semantic_searches',
          'identity.org_auth_config', 'identity.org_logos', 'identity.orgs', 'identity.slug_counters', 'identity.used_reset_links',
          'identity.user_activities', 'identity.user_credentials', 'identity.user_group_members', 'identity.user_groups', 'identity.user_pictures', 'identity.users', 'integrations.slack_threads', 'oauth.access_tokens',
          'oauth.apps', 'oauth.authorization_codes', 'oauth.delegation_events', 'oauth.device_authorizations', 'oauth.refresh_tokens',
          'oauth.service_account_delegations',
          'platform.connectors_config', 'platform.docstore_changes', 'platform.mail_log', 'platform.notifications', 'platform.outbox', 'platform.store_epoch',
        ])
      })

      it('the checked-in Kysely types for the docstore schemas match the migrated catalog', async () => {
        const names = await admin(async (c) => (await c.query(DOCSTORE_TABLES_SQL)).rows.map((r) => `${r.nspname}.${r.relname}`))
        const out = await generateTypes(runtime(), names)
        const file = join(__dirname, '../../../src/libs/repositories/generated/docstore-tables.ts')
        if (process.env.PG_TYPES_UPDATE) writeFileSync(file, out) // regenerate when a docstore revision changes a table
        expect(out).to.equal(readFileSync(file, 'utf8'))
      })
    })
  })
}

/** Every ordinary table of the docstore schemas except the kernel's own tables in schema platform (their types live with the kernel's). */
const DOCSTORE_TABLES_SQL = `SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition
    AND n.nspname IN ('identity', 'conversation', 'content', 'platform', 'oauth', 'integrations')
    AND NOT (n.nspname = 'platform' AND (c.relname IN ('deployment_profile', 'consumer_inbox', 'jobs', 'kv_entries') OR c.relname LIKE 'queue\\_%' OR c.relname LIKE 'sched\\_%'))
  ORDER BY 1, 2`
