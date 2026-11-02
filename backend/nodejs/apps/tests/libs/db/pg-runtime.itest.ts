/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Client, Pool } from 'pg'
import {
  Bouncer, Cluster, PG_BINS, createKernelDb, haveStack, startBouncer, startCluster,
} from './pg-harness'
import {
  ConnectionLost, DbFenced, DbReadOnly, DbTimeout, FailedTransaction, LockNotAvailable, PermissionDenied, PgRuntime,
  PrologueFailed, SchemaNotReady, SessionRoleLeak, UowUsageError, createPgPool, generateTypes, readProfileEpoch,
  registerLockKey, waitForSchema,
} from '../../../src/libs/db'
import { UniqueConstraintError } from '../../../src/libs/errors/database.errors'
import { MissingOrgContext, makeOrgContext, runWithOrgContext } from '../../../src/libs/context/orgContext'

const ORGS = ['aaaaaaaaaaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbbbbbbbbbb', 'cccccccccccccccccccccccc', 'dddddddddddddddddddddddd', 'eeeeeeeeeeeeeeeeeeeeeeee']
const A = ORGS[0]
const inOrg = <T>(orgId: string, fn: () => T) =>
  runWithOrgContext(makeOrgContext({ orgId, userId: `u-${orgId}`, kind: 'user', source: 'jwt', requestId: `r-${orgId}` }), fn)
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const Q = (tag: string, sql: string) => `/* pipeshub:${tag} */ ${sql}`

/** Run `n` tasks with at most `width` in flight, like 3,000 requests arriving over time. */
async function inFlight(n: number, width: number, task: (i: number) => Promise<unknown>): Promise<void> {
  let next = 0
  await Promise.all(Array.from({ length: width }, async () => { while (next < n) await task(next++) }))
}

/** Resolves once `n` callers have arrived: lines two transactions up so a conflict is certain. */
function barrier(n: number) {
  let waiting: Array<() => void> = []
  return () => new Promise<void>((res) => {
    waiting.push(res)
    if (waiting.length >= n) { const w = waiting; waiting = []; w.forEach((r) => r()) }
  })
}

const suite = haveStack ? describe : describe.skip

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`libs/db against PostgreSQL (${bin}) and PgBouncer`, function () {
    this.timeout(120_000)
    let cluster: Cluster
    let reset: Bouncer // kernel reset, tracks prepared statements
    let plain: Bouncer // no reset, max_prepared_statements = 0, one server connection (the worst case)
    const pools: Pool[] = []
    const work = process.env.PG_TEST_WORK ?? tmpdir()

    const rtOn = (port: number, extra: Partial<ConstructorParameters<typeof PgRuntime>[0]> = {}, login = 'pipeshub_node_login', db = 'rt_app') => {
      const pool = createPgPool({ connectionString: cluster.url(db, login, port), service: 'node', version: 'test', max: 10 })
      pools.push(pool)
      return { pool, rt: new PgRuntime({ pool, defaultService: 'node', ...extra }) }
    }

    before(async () => {
      cluster = await startCluster(bin, work)
      await createKernelDb(cluster, 'rt_app')
      await cluster.admin(async (c) => {
        for (const o of ORGS) for (let i = 0; i < 20; i++) await c.query('INSERT INTO kt.docs (org_id, id, body) VALUES ($1, $2, $3)', [o, `d${i}`, `${o}-${i}`])
      }, 'rt_app')
      reset = await startBouncer(cluster, 'rt_app', work, { reset: true, pool: 2, maxPreparedStatements: 200 })
      plain = await startBouncer(cluster, 'rt_app', work, { reset: false, pool: 1, maxPreparedStatements: 0 })
    })

    after(async () => {
      await Promise.all(pools.map((p) => p.end().catch(() => {})))
      reset?.stop()
      plain?.stop()
      cluster?.stop()
    })

    const serverConns = () => cluster.admin(async (c) =>
      (await c.query("SELECT application_name, state, backend_start FROM pg_stat_activity WHERE usename = 'pipeshub_node_login' AND application_name = 'pipeshub-node/test'")).rows)

    it('a missing org is a typed error and nothing reaches the database', async () => {
      const { pool, rt } = rtOn(reset.port)
      const e = await rt.withUow({}, async (tx) => tx.query(Q('t.x', 'SELECT 1'))).catch((x) => x)
      expect(e).to.be.instanceOf(MissingOrgContext)
      expect(pool.totalCount).to.equal(0)
    })

    it('0 cross-org rows over 3,000 pooled transactions, through at most 2 server connections', async () => {
      const { rt } = rtOn(reset.port)
      let cross = 0
      let wrong = 0
      let peak = 0
      const seenConns: unknown[] = []
      const sampler = setInterval(() => { void serverConns().then((rows) => { peak = Math.max(peak, rows.length); if (rows.length > 2) seenConns.push(rows) }) }, 50)
      const one = (i: number) => {
        const org = ORGS[i % ORGS.length] as string
        return inOrg(org, () => rt.withUow({}, async (tx) => {
          // deliberately no org predicate: only the policy keeps other orgs out
          const r = await tx.query<{ org_id: string }>(Q('t.all_docs', 'SELECT org_id FROM kt.docs'))
          cross += r.rows.filter((x) => x.org_id !== org).length
          if (r.rows.length !== 20) wrong++
          const s = await tx.query<{ o: string; rid: string; uid: string }>(
            Q('t.settings', "SELECT current_setting('app.org_id') AS o, current_setting('app.request_id') AS rid, current_setting('app.user_id') AS uid"))
          if (s.rows[0]?.o !== org || s.rows[0]?.rid !== `r-${org}` || s.rows[0]?.uid !== `u-${org}`) wrong++
        }))
      }
      await inFlight(3000, 40, one)
      clearInterval(sampler)
      expect(cross).to.equal(0)
      expect(wrong).to.equal(0)
      expect(peak, JSON.stringify(seenConns[0])).to.be.greaterThan(0).and.at.most(2)
    })

    it('the policy refuses a write for another org, and the login has no table access outside a unit', async () => {
      const { rt, pool } = rtOn(reset.port)
      const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, (tx) =>
        tx.query(Q('t.bad_write', "INSERT INTO kt.docs (org_id, id) VALUES ('bbbbbbbbbbbbbbbbbbbbbbbb', 'evil')")))).catch((x) => x)
      expect(e).to.be.instanceOf(PermissionDenied)
      const raw = await pool.query('SELECT * FROM kt.docs').catch((x) => x)
      expect(raw.code).to.equal('42501')
    })

    it('writes commit in a write unit and fail in a read unit with DbReadOnly (25006)', async () => {
      const { rt } = rtOn(reset.port)
      await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, (tx) => tx.query(Q('t.upd', "UPDATE kt.docs SET version = version + 1 WHERE org_id = $1 AND id = 'd0'"), ['aaaaaaaaaaaaaaaaaaaaaaaa'])))
      const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, (tx) => tx.query(Q('t.upd', "UPDATE kt.docs SET version = version + 1 WHERE org_id = $1 AND id = 'd0'"), ['aaaaaaaaaaaaaaaaaaaaaaaa']))).catch((x) => x)
      expect(e).to.be.instanceOf(DbReadOnly)
    })

    it('prologue failures are typed, run no caller statement, and leave the connection usable', async () => {
      const { rt } = rtOn(reset.port)
      let ran = 0
      for (const service of ['ghost', 'jobs']) {
        const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ service }, async (tx) => { ran++; await tx.query(Q('t.x', 'SELECT 1')) })).catch((x) => x)
        expect(e, service).to.be.instanceOf(PrologueFailed)
      }
      expect(ran).to.equal(0)
      expect(await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async () => 'fine'))).to.equal('fine')
    })

    it('session state left by a client is invisible to the next unit under the kernel reset', async () => {
      const { rt } = rtOn(reset.port)
      for (let i = 0; i < 10; i++) {
        const raw = new Client({ connectionString: cluster.url('rt_app', 'pipeshub_node_login', reset.port) })
        await raw.connect()
        await raw.query('SET ROLE pipeshub_node')
        await raw.query("SET app.org_id = 'org-evil'")
        await raw.query('SET statement_timeout = 1')
        await raw.query('SELECT pg_advisory_lock(4242)')
        await raw.query('CREATE TEMP TABLE leak (a int)')
        await raw.end()
        const seen = await inOrg('bbbbbbbbbbbbbbbbbbbbbbbb', () => rt.withUow({}, async (tx) => {
          const r = await tx.query<any>(Q('t.state', `SELECT current_setting('app.org_id') AS org, current_setting('statement_timeout') AS st,
            (SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid())::int AS locks, to_regclass('pg_temp.leak') AS tmp`))
          return r.rows[0]
        }))
        expect(seen).to.deep.equal({ org: 'bbbbbbbbbbbbbbbbbbbbbbbb', st: '15s', locks: 0, tmp: null })
      }
    })

    it('with no pooler reset the guard catches a leaked session role 10 times out of 10', async () => {
      const { rt } = rtOn(plain.port)
      let leaks = 0
      let ran = 0
      for (let i = 0; i < 10; i++) {
        const raw = new Client({ connectionString: cluster.url('rt_app', 'pipeshub_node_login', plain.port) })
        await raw.connect()
        await raw.query('SET ROLE pipeshub_node')
        await raw.end()
        const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async () => { ran++ })).catch((x) => x)
        if (e instanceof SessionRoleLeak) leaks++
      }
      expect(leaks).to.equal(10)
      expect(ran).to.equal(0)
    })

    it('unnamed statements work behind a pooler that tracks no prepared statements (3,000 units, 0 errors)', async () => {
      const { rt } = rtOn(plain.port, { pooler: 'azure_pgbouncer' })
      // clear whatever the previous test left on the single server connection
      const fix = new Client({ connectionString: cluster.url('rt_app', 'pipeshub_node_login', plain.port) })
      await fix.connect(); await fix.query('RESET ROLE'); await fix.end()
      let errors = 0
      let named = 0
      let firstError: unknown
      await inFlight(3000, 40, (i) => inOrg(ORGS[i % 5] as string, () => rt.withUow({}, async (tx) => {
        const r = await tx.query<{ n: number }>(Q('t.count', 'SELECT count(*)::int AS n FROM kt.docs WHERE org_id = $1'), [ORGS[i % 5]])
        const p = await tx.query<{ n: number }>(Q('t.prepared', 'SELECT count(*)::int AS n FROM pg_prepared_statements'))
        if (r.rows[0]?.n !== 20) errors++
        named += p.rows[0]?.n ?? 0
      })).catch((e) => { errors++; firstError ??= e }))
      expect(errors, String(firstError)).to.equal(0)
      expect(named).to.equal(0)
      const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ customPlan: true }, (tx) => tx.query({ name: 'q', text: Q('t.q', 'SELECT 1') }))).catch((x) => x)
      expect(e).to.be.instanceOf(UowUsageError)
    })

    it('named statements run in a customPlan unit through the tracking pooler (parity with the driver check)', async () => {
      const { rt } = rtOn(reset.port)
      let errors = 0
      await Promise.all(Array.from({ length: 300 }, (_, i) => inOrg(ORGS[i % 5] as string, () => rt.withUow({ customPlan: true }, async (tx) => {
        const r = await tx.query<{ n: number }>({ name: 'count_docs', text: Q('t.named', 'SELECT count(*)::int AS n FROM kt.docs WHERE org_id = $1'), values: [ORGS[i % 5]] })
        if (r.rows[0]?.n !== 20) errors++
      })).catch(() => { errors++ })))
      expect(errors).to.equal(0)
    })

    it('a serialization conflict is retried and both units commit', async () => {
      const { rt } = rtOn(reset.port)
      await cleanCounters()
      const sync = barrier(2)
      let attempts = 0
      // read committed cannot conflict on its own: the first attempt of each unit gets a server-raised 40001
      const bump = (k: string) => inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, async (tx) => {
        const first = ++attempts <= 2
        await tx.query(Q('t.bump', 'UPDATE kt.counter SET n = n + 1 WHERE k = $1'), [k])
        if (first) {
          await sync()
          await tx.query(Q('t.fail', "DO $$ BEGIN RAISE EXCEPTION 'conflict' USING ERRCODE = '40001'; END $$"))
        }
      }))
      await Promise.all([bump('a'), bump('b')])
      expect((await counter('a')) + (await counter('b'))).to.equal(2)
      expect(attempts).to.be.greaterThan(2)
    })

    it('a deadlock is retried and both units commit', async () => {
      const { rt } = rtOn(reset.port)
      await cleanCounters()
      const sync = barrier(2)
      let runs = 0
      const lock = (first: string, second: string) => inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, async (tx) => {
        const initial = ++runs <= 2
        await tx.query(Q('t.lock1', 'SELECT 1 FROM kt.counter WHERE k = $1 FOR UPDATE'), [first])
        if (initial) await sync()
        await tx.query(Q('t.lock2', 'UPDATE kt.counter SET n = n + 1 WHERE k = $1'), [second])
      }))
      await Promise.all([lock('a', 'b'), lock('b', 'a')])
      expect(runs).to.be.greaterThan(2)
      expect((await counter('a')) + (await counter('b'))).to.equal(2)
    })

    it('a statement timeout raises DbTimeout and the next unit on that server connection has the default', async () => {
      const { rt } = rtOn(reset.port)
      const t0 = Date.now()
      const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ timeoutMs: 50 }, (tx) => tx.query(Q('t.slow', 'SELECT pg_sleep(5)')))).catch((x) => x)
      expect(e).to.be.instanceOf(DbTimeout)
      expect(Date.now() - t0).to.be.lessThan(2000)
      const after = await Promise.all(Array.from({ length: 6 }, () => inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async (tx) =>
        (await tx.query<any>(Q('t.st', "SELECT current_setting('statement_timeout') AS st, current_setting('lock_timeout') AS lt"))).rows[0]))))
      for (const r of after) expect(r).to.deep.equal({ st: '15s', lt: '2s' })
    })

    it('a lock timeout raises LockNotAvailable without a retry', async () => {
      const { rt } = rtOn(reset.port)
      const holding = barrier(2)
      const done = barrier(2)
      const holder = inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, async (tx) => {
        await tx.query(Q('t.hold', "SELECT 1 FROM kt.counter WHERE k = 'a' FOR UPDATE"))
        await holding(); await done()
      }))
      await holding()
      let runs = 0
      const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true, lockTimeoutMs: 100 }, async (tx) => {
        runs++
        await tx.query(Q('t.wait', "SELECT 1 FROM kt.counter WHERE k = 'a' FOR UPDATE"))
      })).catch((x) => x)
      await done(); await holder
      expect(e).to.be.instanceOf(LockNotAvailable)
      expect(runs).to.equal(1)
    })

    it('a swallowed error without a savepoint surfaces as FailedTransaction (25P02), a duplicate key as UniqueConstraintError', async () => {
      const { rt } = rtOn(reset.port)
      const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, async (tx) => {
        const dup = await tx.query(Q('t.dup', "INSERT INTO kt.docs (org_id, id) VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 'd1')")).catch((x) => x)
        expect(dup).to.be.instanceOf(UniqueConstraintError)
        await tx.query(Q('t.after', 'SELECT 1'))
      })).catch((x) => x)
      expect(e).to.be.instanceOf(FailedTransaction)
    })

    describe('a statement that ends the transaction early is never reported as success', () => {
      // each passes the text guard (it does not parse quoting the way the server does); the server runs the COMMIT
      const bypasses: Record<string, string> = {
        'E-string with an escaped quote': "SELECT E'\\''; COMMIT; BEGIN; SELECT '--'",
        'comment opener inside a string': "SELECT '/*'; COMMIT; BEGIN; SELECT '*/'",
        'comment marker inside a string': "SELECT '--'; COMMIT; BEGIN\n",
        'dollar quote inside a quoted identifier': 'SELECT 1 AS "$$"; COMMIT; BEGIN; SELECT 1 AS "$$"',
      }
      const rowsOf = (prefix: string) => cluster.admin(async (c) =>
        (await c.query('SELECT id FROM kt.docs WHERE id LIKE $1 ORDER BY id', [`${prefix}%`])).rows.map((r) => r.id), 'rt_app')
      const insert = (tx: any, id: string) => tx.query(Q('t.g60', 'INSERT INTO kt.docs (org_id, id) VALUES ($1, $2)'), [A, id])
      after(() => cluster.admin((c) => c.query("DELETE FROM kt.docs WHERE id LIKE 'g60-%'"), 'rt_app'))

      it('the text guard refuses plain COMMIT; BEGIN, and nothing of the unit is kept', async () => {
        const { rt } = rtOn(reset.port)
        const e = await inOrg(A, () => rt.withUow({ write: true }, async (tx) => {
          await insert(tx, 'g60-plain-1')
          await tx.query(Q('t.g60', 'SELECT 1; COMMIT; BEGIN'))
        })).catch((x) => x)
        expect(e).to.be.instanceOf(UowUsageError)
        expect(await rowsOf('g60-plain')).to.deep.equal([])
      })

      for (const [name, sql] of Object.entries(bypasses)) {
        it(`${name}: FailedTransaction, the rest of the unit is rolled back and the connection is discarded`, async () => {
          const { rt, pool } = rtOn(reset.port)
          const tag = `g60-${Object.keys(bypasses).indexOf(name)}`
          let after: unknown
          const e = await inOrg(A, () => rt.withUow({ write: true }, async (tx) => {
            await insert(tx, `${tag}-before`)
            await tx.query(Q('t.g60', sql))
            // the new transaction has no role and no org: the policy layer refuses what follows
            after = await insert(tx, `${tag}-after`).catch((x: unknown) => x)
          })).catch((x) => x)
          expect(after).to.be.instanceOf(PermissionDenied)
          expect(e).to.be.instanceOf(FailedTransaction)
          // the part before the smuggled COMMIT is committed (the server did it); nothing after it is
          expect(await rowsOf(tag)).to.deep.equal([`${tag}-before`])
          expect(pool.totalCount).to.equal(0) // the connection was destroyed, not returned to the pool
        })
      }

      it('a bare COMMIT leaves the connection idle: caught right after that statement (transaction status), later statements never run', async () => {
        const { rt } = rtOn(reset.port)
        const e = await inOrg(A, () => rt.withUow({ write: true }, async (tx) => {
          await tx.query(Q('t.g60', "SELECT '--'; COMMIT\n"))
          await insert(tx, 'g60-bare-after')
        })).catch((x) => x)
        expect(e).to.be.instanceOf(FailedTransaction)
        expect(await rowsOf('g60-bare')).to.deep.equal([])
      })

      it('a DO block cannot end the transaction: the server refuses it and the unit fails', async () => {
        const { rt } = rtOn(reset.port)
        const e = await inOrg(A, () => rt.withUow({ write: true }, async (tx) => {
          await insert(tx, 'g60-do-before')
          await tx.query(Q('t.g60', 'DO $$ BEGIN COMMIT; END $$'))
          await insert(tx, 'g60-do-after')
        })).catch((x) => x)
        expect(e).to.be.instanceOf(Error)
        expect(await rowsOf('g60-do')).to.deep.equal([])
      })
    })

    it('a connection killed mid-transaction raises; it is retried only for an idempotent unit', async () => {
      const { rt } = rtOn(cluster.port) // direct: pg_terminate_backend must hit this unit's backend
      const kill = async (tx: any) => {
        const pid = (await tx.query(Q('t.pid', 'SELECT pg_backend_pid() AS pid'))).rows[0].pid
        await cluster.admin((c) => c.query('SELECT pg_terminate_backend($1)', [pid]))
        await sleep(100)
        await tx.query(Q('t.after_kill', 'SELECT 1'))
      }
      let runs = 0
      const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async (tx) => { runs++; await kill(tx) })).catch((x) => x)
      expect(e).to.be.instanceOf(ConnectionLost)
      expect(runs).to.equal(1)
      let idem = 0
      await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ idempotent: true }, async (tx) => { if (++idem === 1) await kill(tx); else await tx.query(Q('t.ok', 'SELECT 1')) }))
      expect(idem).to.equal(2)
    })

    it('transaction-scoped advisory locks with registered keys: exclusion within, release at commit, nothing left on the session', async () => {
      const { rt } = rtOn(reset.port)
      const key = registerLockKey(`it.lock.${cluster.port}`, 7_340_500 + cluster.port)
      const holding = barrier(2)
      const release = barrier(2)
      const a = inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async (tx) => { await tx.advisoryXactLock(key, 1); await holding(); await release() }))
      await holding()
      const during = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, (tx) => tx.tryAdvisoryXactLock(key, 1)))
      const other = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, (tx) => tx.tryAdvisoryXactLock(key, 2)))
      await release(); await a
      const after = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, (tx) => tx.tryAdvisoryXactLock(key, 1)))
      expect([during, other, after]).to.deep.equal([false, true, true])
      const left = await cluster.admin(async (c) => Number((await c.query("SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory'")).rows[0].n))
      expect(left).to.equal(0)
    })

    describe('write fence', () => {
      const fence = (epoch: number) => ({ fn: 'kernel.assert_profile_epoch', epoch })

      it('passes with the right epoch, fails with DbFenced on a stale one; reads are unaffected', async () => {
        const ok = rtOn(reset.port, { fence: fence(1) }).rt
        await inOrg(A, () => ok.withUow({ write: true }, (tx) => tx.query(Q('t.w', 'SELECT 1'))))
        const stale = rtOn(reset.port, { fence: fence(2) }).rt
        const e = await inOrg(A, () => stale.withUow({ write: true }, (tx) => tx.query(Q('t.w', 'SELECT 1')))).catch((x) => x)
        expect(e).to.be.instanceOf(DbFenced)
        await inOrg(A, () => stale.withUow({}, (tx) => tx.query(Q('t.r', 'SELECT 1'))))
      })

      it('the server asserts read committed too (a client that skipped the check is stopped)', async () => {
        const c = new Client({ host: '127.0.0.1', port: cluster.port, user: 'postgres', database: 'rt_app' })
        await c.connect()
        await c.query('BEGIN ISOLATION LEVEL REPEATABLE READ')
        const e = await c.query('SELECT kernel.assert_profile_epoch(1)').catch((x) => x)
        await c.query('ROLLBACK'); await c.end()
        expect(e.code).to.equal('25000')
      })
    })

    it('jobs units run on the jobs pool and role and read all rows (the jobs policy), with or without an org', async () => {
      const jobs = rtOn(reset.port, {}, 'pipeshub_jobs')
      const rt = new PgRuntime({ pool: rtOn(reset.port).pool, jobsPool: jobs.pool, defaultService: 'node' })
      const { system, forOrg } = await import('../../../src/libs/context/orgContext')
      const sys = await system('sweep', () => rt.withJobsUow({}, async (tx) =>
        (await tx.query<any>(Q('t.jobs_docs', "SELECT count(*)::int AS n, current_user AS u FROM kt.docs"))).rows[0]))
      expect(sys).to.deep.equal({ n: 100, u: 'pipeshub_jobs' })
      const one = await forOrg('cccccccccccccccccccccccc', 'sweep', () => rt.withJobsUow({}, async (tx) =>
        (await tx.query<any>(Q('t.jobs_docs', 'SELECT count(*)::int AS n FROM kt.docs'))).rows[0]))
      expect(one).to.deep.equal({ n: 100 })
    })

    it('Kysely runs inside the unit under the same policy', async () => {
      const { rt } = rtOn(reset.port)
      type DB = { 'kt.docs': { org_id: string; id: string; body: string | null; version: number } }
      const rows = await inOrg('dddddddddddddddddddddddd', () => rt.withUow({}, (tx) =>
        tx.db<DB>('t.kysely').selectFrom('kt.docs').select(['org_id', 'id']).orderBy('id').limit(3).execute()))
      expect(rows.map((r) => r.org_id)).to.deep.equal(['dddddddddddddddddddddddd', 'dddddddddddddddddddddddd', 'dddddddddddddddddddddddd'])
    })

    it('typegen: Kysely types for the fixture tables come from the catalog', async () => {
      const { rt } = rtOn(reset.port)
      const out = await generateTypes(rt, ['kt.docs', 'kt.counter'])
      expect(out).to.contain('export interface KtDocs {\n  org_id: string;\n  id: string;\n  body: string | null;\n  version: Generated<number>;\n}')
      expect(out).to.contain('export interface KtCounter {\n  k: string;\n  n: Generated<number>;\n  note: string | null;\n}')
      const e = await generateTypes(rt, ['kt.nope']).catch((x) => x)
      expect(e.message).to.contain('kt.nope')
    })

    describe('against the migrated kernel schema', () => {
      const kernelRt = (extra = {}) => rtOn(reset.port, extra).rt

      it('the boot assert reads kernel.schema_epoch as the service role', async () => {
        const rt = kernelRt()
        await waitForSchema(rt, { kernel: { requires: 2, code: 11 } })
        const e = await waitForSchema(rt, { kernel: { requires: 99, code: 11 } }, { attempts: 2, delayMs: 1 }).catch((x) => x)
        expect(e).to.be.instanceOf(SchemaNotReady)
        expect(e.problems[0]).to.contain('< required 99')
      })

      it('lineage: true sets app.org_lineage from the real kernel.org_lineage (comma list, the org first)', async () => {
        const r = await inOrg(A, () => kernelRt().withUow({ lineage: true }, (tx) => tx.query(Q('t.lin', "SELECT current_setting('app.org_lineage') AS l"))))
        expect(r.rows[0].l).to.equal(A)
      })

      it('the profile epoch is read through the fence function and a stale one is DbFenced (55000)', async () => {
        const epoch = await readProfileEpoch(kernelRt())
        expect(epoch).to.equal(1)
        const stale = kernelRt({ fence: { fn: 'kernel.assert_profile_epoch', epoch: 7 } })
        const e = await inOrg(A, () => stale.withUow({ write: true }, (tx) => tx.query(Q('t.w', 'SELECT 1')))).catch((x) => x)
        expect(e).to.be.instanceOf(DbFenced)
      })

      it('the tenancy catalog and the security surface are clean with the fixture schema in place', async () => {
        const rows = await cluster.admin(async (c) => (await c.query('SELECT rule, object FROM kernel.rls_violations')).rows, 'rt_app')
        expect(rows).to.deep.equal([])
      })

      it('the checked-in kernel table types match the migrated catalog', async () => {
        const out = await generateTypes(kernelRt(), ['kernel.completed_jobs', 'kernel.schema_epoch', 'platform.deployment_profile'])
        const file = join(__dirname, '../../../src/libs/db/generated/kernel-tables.ts')
        if (process.env.PG_TYPES_UPDATE) writeFileSync(file, out) // regenerate after a kernel revision changes these tables
        expect(out).to.equal(readFileSync(file, 'utf8'))
      })
    })

    async function counter(k: string): Promise<number> {
      return cluster.admin(async (c) => Number((await c.query('SELECT n FROM kt.counter WHERE k = $1', [k])).rows[0].n), 'rt_app')
    }
    async function cleanCounters(): Promise<void> {
      await cluster.admin((c) => c.query('UPDATE kt.counter SET n = 0'), 'rt_app')
    }
  })
}
