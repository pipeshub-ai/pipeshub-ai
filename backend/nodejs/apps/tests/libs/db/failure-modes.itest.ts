/// <reference types="mocha" />
// Failure-mode integration tests: fault injection through a TCP proxy, killed connections, commit outcomes, nesting, savepoints.
// Run with `npm run test:pg` (needs PG_TEST_BINS and PG_TEST_PGBOUNCER).
import 'reflect-metadata'
import { expect } from 'chai'
import { EventEmitter } from 'node:events'
import { Server, Socket, connect, createServer } from 'node:net'
import { pipeline, Readable, Writable } from 'node:stream'
import { promisify } from 'node:util'
import { tmpdir } from 'node:os'
import { Client, Pool } from 'pg'
import { Bouncer, Cluster, PG_BINS, createKernelDb, haveStack, startBouncer, startCluster } from './pg-harness'
import {
  ConnectionLost, FailedTransaction, PgRuntime, PrologueFailed, SerializationFailure, DeadlockDetected,
  SessionRoleLeak, UowUsageError, WriteIsolationRefused, createPgPool, DbTimeout, LockNotAvailable,
} from '../../../src/libs/db'
import { UniqueConstraintError } from '../../../src/libs/errors/database.errors'
import { MissingOrgContext, currentOrgContext, makeOrgContext, runWithOrgContext, spawnDetached } from '../../../src/libs/context/orgContext'

const ORGS = ['aaaaaaaaaaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbbbbbbbbbb', 'cccccccccccccccccccccccc', 'dddddddddddddddddddddddd', 'eeeeeeeeeeeeeeeeeeeeeeee']
const inOrg = <T>(orgId: string, fn: () => T) =>
  runWithOrgContext(makeOrgContext({ orgId, userId: `u-${orgId}`, kind: 'user', source: 'jwt', requestId: `r-${orgId}` }), fn)
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
const Q = (tag: string, sql: string) => `/* pipeshub:${tag} */ ${sql}`
const pipe = promisify(pipeline)

async function inFlight(n: number, width: number, task: (i: number) => Promise<unknown>): Promise<void> {
  let next = 0
  await Promise.all(Array.from({ length: width }, async () => { while (next < n) await task(next++) }))
}

/** TCP proxy in front of the cluster; `hook` decides what happens to each client->server chunk. */
type Action = 'forward' | 'kill' | 'forward_then_kill'
class Proxy {
  server!: Server
  port = 0
  conns = 0
  sockets: Socket[] = []
  constructor(private target: number, public hook: (chunk: string, conn: number) => Action) {}
  async start(): Promise<void> {
    this.server = createServer((c) => {
      const idx = ++this.conns
      const u = connect(this.target, '127.0.0.1')
      this.sockets.push(c, u)
      let dead = false
      const die = () => { dead = true; c.destroy(); u.destroy() }
      u.on('data', (d) => { if (!dead) c.write(d) })
      u.on('error', () => c.destroy()); c.on('error', () => u.destroy())
      u.on('close', () => c.destroy()); c.on('close', () => u.destroy())
      c.on('data', (d) => {
        const a = this.hook(d.toString('latin1'), idx)
        if (a === 'forward') u.write(d)
        else if (a === 'kill') die()
        else { u.write(d); dead = true; setTimeout(() => { c.destroy(); u.destroy() }, 150) } // server gets it, client never hears back
      })
    })
    await new Promise<void>((r) => this.server.listen(0, '127.0.0.1', r))
    this.port = (this.server.address() as { port: number }).port
  }
  stop(): void { this.sockets.forEach((s) => s.destroy()); this.server.close() }
}

const suite = haveStack ? describe : describe.skip

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`libs/db failure modes (${bin})`, function () {
    this.timeout(180_000)
    let cluster: Cluster
    let reset: Bouncer
    let plain: Bouncer
    const pools: Pool[] = []
    const work = process.env.PG_TEST_WORK ?? tmpdir()
    const uncaught: Error[] = []
    const onUncaught = (e: Error) => { uncaught.push(e) }

    const rtOn = (port: number, extra: Partial<ConstructorParameters<typeof PgRuntime>[0]> = {}, max = 10, login = 'pipeshub_node_login') => {
      const pool = createPgPool({ connectionString: cluster.url('fm_app', login, port), service: 'node', version: 'test', max })
      pools.push(pool)
      return { pool, rt: new PgRuntime({ pool, defaultService: 'node', ...extra }) }
    }
    const val = (sql: string, args: unknown[] = []) => cluster.admin(async (c) => (await c.query(sql, args)).rows, 'fm_app')

    before(async () => {
      process.on('uncaughtException', onUncaught)
      cluster = await startCluster(bin, work)
      await createKernelDb(cluster, 'fm_app')
      await cluster.admin(async (c) => {
        for (const o of ORGS) for (let i = 0; i < 20; i++) await c.query('INSERT INTO kt.docs (org_id, id, body) VALUES ($1, $2, $3)', [o, `d${i}`, `${o}-${i}`])
      }, 'fm_app')
      reset = await startBouncer(cluster, 'fm_app', work, { reset: true, pool: 2, maxPreparedStatements: 200 })
      plain = await startBouncer(cluster, 'fm_app', work, { reset: false, pool: 1, maxPreparedStatements: 0 })
    })
    after(async () => {
      process.off('uncaughtException', onUncaught)
      await Promise.all(pools.map((p) => p.end().catch(() => {})))
      reset?.stop(); plain?.stop(); cluster?.stop()
    })
    beforeEach(async () => { await cluster.admin((c) => c.query("UPDATE kt.counter SET n = 0; DELETE FROM kt.docs WHERE id LIKE 't-%'"), 'fm_app') })

    // ---------------------------------------------------------------- COMMIT outcome unknown
    describe('commit outcome', () => {
      for (const idempotent of [false, true]) {
        it(`COMMIT reaches the server, the answer never returns (idempotent=${idempotent}): applied once, never retried, ConnectionLost.commitSent`, async () => {
          const px = new Proxy(cluster.port, (c) => (/^Q\0*.*COMMIT\0/s.test(c) ? 'forward_then_kill' : 'forward'))
          await px.start()
          try {
            const { rt } = rtOn(px.port)
            let runs = 0
            const e: any = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true, idempotent }, async (tx) => {
              runs++
              await tx.query(Q('t.inc', "UPDATE kt.counter SET n = n + 1 WHERE k = 'a'"))
            })).catch((x) => x)
            expect(e, String(e)).to.be.instanceOf(ConnectionLost)
            expect(e.commitSent).to.equal(true)
            expect(runs).to.equal(1)
            expect(px.conns, 'no second connection was opened for a retry').to.equal(1)
            await sleep(300)
            expect(Number((await val("SELECT n FROM kt.counter WHERE k='a'"))[0].n)).to.equal(1)
          } finally { px.stop() }
        })

        it(`the connection dies as COMMIT is sent, before the server reads it (idempotent=${idempotent}): not retried`, async () => {
          const px = new Proxy(cluster.port, (c) => (/^Q\0*.*COMMIT\0/s.test(c) ? 'kill' : 'forward'))
          await px.start()
          try {
            const { rt } = rtOn(px.port)
            let runs = 0
            const e: any = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true, idempotent }, async (tx) => {
              runs++
              await tx.query(Q('t.inc', "UPDATE kt.counter SET n = n + 1 WHERE k = 'a'"))
            })).catch((x) => x)
            expect(e).to.be.instanceOf(ConnectionLost)
            expect(e.commitSent).to.equal(true)
            expect(runs).to.equal(1)
            expect(px.conns).to.equal(1)
            expect(Number((await val("SELECT n FROM kt.counter WHERE k='a'"))[0].n)).to.equal(0)
          } finally { px.stop() }
        })
      }

      it('pg_terminate_backend while COMMIT waits on a slow deferred trigger: ConnectionLost.commitSent, one run', async () => {
        await cluster.admin((c) => c.query(`
          CREATE OR REPLACE FUNCTION kt.slow_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(1.5); RETURN NULL; END $$;
          DROP TRIGGER IF EXISTS slow ON kt.counter;
          CREATE CONSTRAINT TRIGGER slow AFTER UPDATE ON kt.counter DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION kt.slow_commit();`), 'fm_app')
        try {
          const { rt } = rtOn(cluster.port)
          let runs = 0
          let pid = 0
          const p = inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true, idempotent: true }, async (tx) => {
            runs++
            pid = (await tx.query<{ pid: number }>(Q('t.pid', 'SELECT pg_backend_pid() AS pid'))).rows[0]!.pid
            await tx.query(Q('t.inc', "UPDATE kt.counter SET n = n + 1 WHERE k = 'a'"))
          })).catch((x) => x)
          while (!pid) await sleep(10)
          await sleep(400) // the COMMIT is now inside the deferred trigger
          await cluster.admin((c) => c.query('SELECT pg_terminate_backend($1)', [pid]))
          const e: any = await p
          expect(e).to.be.instanceOf(ConnectionLost)
          expect(e.commitSent).to.equal(true)
          expect(runs).to.equal(1)
        } finally {
          await cluster.admin((c) => c.query('DROP TRIGGER IF EXISTS slow ON kt.counter'), 'fm_app')
        }
      })

      it('connection loss before the first statement (the prologue) is retried exactly once; a second loss surfaces', async () => {
        let kills = 1
        const px = new Proxy(cluster.port, (c) => (kills > 0 && /WITH pre AS/.test(c) ? (kills--, 'kill') : 'forward'))
        await px.start()
        try {
          const { rt } = rtOn(px.port)
          let runs = 0
          await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async (tx) => { runs++; await tx.query(Q('t.ok', 'SELECT 1')) }))
          expect(runs).to.equal(1)
          kills = 2
          runs = 0
          const e: any = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async () => { runs++ })).catch((x) => x)
          expect(e).to.be.instanceOf(ConnectionLost)
          expect(runs).to.equal(0)
        } finally { px.stop() }
      })

      it('mid-callable loss: non-idempotent is not retried; idempotent is retried exactly once', async () => {
        let killNext = 0
        const px = new Proxy(cluster.port, (c) => (killNext > 0 && /t\.mid/.test(c) ? (killNext--, 'kill') : 'forward'))
        await px.start()
        try {
          const { rt } = rtOn(px.port)
          killNext = 5
          let runs = 0
          let e: any = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async (tx) => { runs++; await tx.query(Q('t.mid', 'SELECT 1')) })).catch((x) => x)
          expect(e).to.be.instanceOf(ConnectionLost); expect(e.commitSent).to.equal(false); expect(runs).to.equal(1)
          runs = 0
          e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ idempotent: true }, async (tx) => { runs++; await tx.query(Q('t.mid', 'SELECT 1')) })).catch((x) => x)
          expect(e).to.be.instanceOf(ConnectionLost)
          expect(runs, 'one retry only').to.equal(2)
        } finally { px.stop() }
      })

      it('retry table through the real server: 40001 and 40P01 retried 3x (4 runs), 57014 and 55P03 never, 23505 never', async () => {
        const { rt } = rtOn(reset.port)
        const raise = (tx: any, code: string) => tx.query(Q('t.raise', `DO $$ BEGIN RAISE EXCEPTION 'x' USING ERRCODE = '${code}'; END $$`))
        const cases: Array<[string, any, number]> = [
          ['40001', SerializationFailure, 4], ['40P01', DeadlockDetected, 4], ['57014', DbTimeout, 1], ['55P03', LockNotAvailable, 1], ['23505', UniqueConstraintError, 1],
        ]
        for (const [code, cls, expectedRuns] of cases) {
          let runs = 0
          const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, async (tx) => { runs++; await raise(tx, code) })).catch((x) => x)
          expect(e, code).to.be.instanceOf(cls)
          expect(runs, code).to.equal(expectedRuns)
        }
      })

      it('a 40001 raised mid-unit is retried and both units commit; a serializable write unit is refused', async () => {
        const { rt } = rtOn(reset.port)
        let arrived = 0
        const both = async () => { arrived++; while (arrived < 2) await sleep(5) }
        const runs = [0, 0]
        const mk = (i: number, k: string) => inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, async (tx) => {
          runs[i]!++
          await tx.query(Q('t.set', `UPDATE kt.counter SET n = n + 1 WHERE k = '${k}'`))
          if (runs[i] === 1) {
            await both()
            await tx.query(Q('t.fail', "DO $$ BEGIN RAISE EXCEPTION 'conflict' USING ERRCODE = '40001'; END $$"))
          }
        }))
        const refused = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true, isolation: 'serializable' }, async () => 1)).catch((x) => x)
        expect(refused).to.be.instanceOf(WriteIsolationRefused)
        await Promise.all([mk(0, 'a'), mk(1, 'b')])
        expect(runs[0]! + runs[1]!).to.be.greaterThan(2)
        expect(await val('SELECT sum(n)::int AS s FROM kt.counter').then((r) => r[0].s)).to.equal(2)
      })
    })

    // ---------------------------------------------------------------- pool error handler
    describe('killed connections do not crash the process', () => {
      it('killed between statements while the callable is idle: typed ConnectionLost, no uncaught exception, pool recovers', async () => {
        const before = uncaught.length
        const { rt } = rtOn(cluster.port)
        const e: any = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async (tx) => {
          const pid = (await tx.query<{ pid: number }>(Q('t.pid', 'SELECT pg_backend_pid() AS pid'))).rows[0]!.pid
          await cluster.admin((c) => c.query('SELECT pg_terminate_backend($1)', [pid]))
          await sleep(300) // error event fires here, with no query in flight
        })).catch((x) => x)
        expect(e).to.be.instanceOf(ConnectionLost)
        expect(e.commitSent).to.equal(true) // COMMIT on a dead client: outcome unknown, so no retry
        await sleep(200)
        expect(uncaught.length).to.equal(before)
        await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, (tx) => tx.query(Q('t.ok', 'SELECT 1'))))
      })

      it('killed while idle in the pool: no uncaught exception, next unit works', async () => {
        const before = uncaught.length
        const { rt } = rtOn(cluster.port)
        await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, (tx) => tx.query(Q('t.ok', 'SELECT 1'))))
        await cluster.admin((c) => c.query("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = 'pipeshub-node/test' AND state = 'idle'"))
        await sleep(300)
        await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, (tx) => tx.query(Q('t.ok', 'SELECT 1'))))
        expect(uncaught.length).to.equal(before)
      })

      it('killed repeatedly at random points under load: only typed errors, no uncaught exceptions', async () => {
        const before = uncaught.length
        const { rt } = rtOn(cluster.port, {}, 6)
        const kinds: Record<string, number> = {}
        const killer = setInterval(() => {
          void cluster.admin((c) => c.query("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = 'pipeshub-node/test' AND pid <> pg_backend_pid() ORDER BY random() LIMIT 1")).catch(() => {})
        }, 25)
        await inFlight(1500, 12, async (i) => {
          await inOrg(ORGS[i % 5]!, () => rt.withUow({ write: i % 3 === 0 }, async (tx) => {
            await tx.query(Q('t.a', 'SELECT 1'))
            await sleep(3)
            await tx.query(Q('t.b', 'SELECT 1'))
          })).catch((e) => { kinds[e.constructor.name] = (kinds[e.constructor.name] ?? 0) + 1 })
        })
        clearInterval(killer)
        expect(Object.keys(kinds).filter((k) => k !== 'ConnectionLost'), JSON.stringify(kinds)).to.deep.equal([])
        expect(uncaught.length).to.equal(before)
        expect(kinds.ConnectionLost ?? 0).to.be.greaterThan(0)
      })
    })

    // ---------------------------------------------------------------- decisions
    describe('decisions', () => {
      it('a unit inside a unit is refused immediately, same org or other, and cannot exhaust the pool', async () => {
        const { rt, pool } = rtOn(reset.port, {}, 2)
        const same = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async () => rt.withUow({}, async (tx2) => tx2.orgId))).catch((x) => x)
        expect(same).to.be.instanceOf(UowUsageError)
        const t0 = Date.now()
        const res = await Promise.all([0, 1].map(() => inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async () => {
          await sleep(50)
          return rt.withUow({}, async (tx) => tx.orgId)
        })).catch((x) => x)))
        expect(res.every((r) => r instanceof UowUsageError)).to.equal(true)
        expect(Date.now() - t0).to.be.lessThan(1000)
        expect(pool.waitingCount).to.equal(0)
      })

      it('a swallowed statement error is never reported as success, with or without a later statement', async () => {
        const { rt } = rtOn(reset.port)
        await val("INSERT INTO kt.docs (org_id, id) VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 't-dup')")
        const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, async (tx) => {
          await tx.query(Q('t.dup', "INSERT INTO kt.docs (org_id, id) VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 't-dup')")).catch(() => {})
          await tx.query(Q('t.next', 'SELECT 1'))
        })).catch((x) => x)
        expect(e).to.be.instanceOf(FailedTransaction)

        const e2 = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, async (tx) => {
          await tx.query(Q('t.ins', "INSERT INTO kt.docs (org_id, id) VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 't-keep')"))
          await tx.query(Q('t.dup', "INSERT INTO kt.docs (org_id, id) VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 't-dup')")).catch(() => {})
          return 'returned-ok'
        })).catch((x) => x)
        expect(e2).to.be.instanceOf(FailedTransaction)
        expect((await val("SELECT count(*)::int AS n FROM kt.docs WHERE id = 't-keep'"))[0].n).to.equal(0)
      })

      it('tx.savepoint recovers from a handled duplicate key and the unit commits its other writes', async () => {
        const { rt } = rtOn(reset.port)
        await val("INSERT INTO kt.docs (org_id, id) VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 't-dup2')")
        const out = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, async (tx) => {
          await tx.query(Q('t.ins', "INSERT INTO kt.docs (org_id, id) VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 't-sp-keep')"))
          const dup = await tx.savepoint('try_dup', () => tx.query(Q('t.dup', "INSERT INTO kt.docs (org_id, id) VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 't-dup2')"))).catch((x) => x)
          await tx.query(Q('t.after', 'SELECT 1'))
          return dup
        }))
        expect(out).to.be.instanceOf(UniqueConstraintError)
        expect((await val("SELECT count(*)::int AS n FROM kt.docs WHERE id = 't-sp-keep'"))[0].n).to.equal(1)
      })

      it('ON CONFLICT DO NOTHING RETURNING works for the duplicate case', async () => {
        const { rt } = rtOn(reset.port)
        await val("INSERT INTO kt.docs (org_id, id) VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 't-dup3')")
        const r = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, (tx) =>
          tx.query(Q('t.oc', "INSERT INTO kt.docs (org_id, id) VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 't-dup3') ON CONFLICT DO NOTHING RETURNING id"))))
        expect(r.rows).to.have.length(0)
      })
    })

    // ---------------------------------------------------------------- safety
    describe('safety', () => {
      it('org binding ignores opts and is fixed for the unit; re-entering another org context is refused', async () => {
        const { rt } = rtOn(reset.port)
        const seen = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ orgId: 'bbbbbbbbbbbbbbbbbbbbbbbb', org: 'bbbbbbbbbbbbbbbbbbbbbbbb' } as any, async (tx) => {
          const rebind = await Promise.resolve().then(() => inOrg('cccccccccccccccccccccccc', () => 1)).catch((e) => e.constructor.name)
          const inner = (await tx.query<{ o: string }>(Q('t.o', "SELECT current_setting('app.org_id') AS o"))).rows[0]!.o
          return { txOrg: tx.orgId, inner, rebind }
        }))
        expect(seen).to.deep.equal({ txOrg: 'aaaaaaaaaaaaaaaaaaaaaaaa', inner: 'aaaaaaaaaaaaaaaaaaaaaaaa', rebind: 'OrgMismatch' })
      })

      it('a callable cannot re-bind the org or role with SET LOCAL or set_config', async () => {
        const { rt } = rtOn(reset.port)
        for (const sql of ["SET LOCAL app.org_id = 'bbbbbbbbbbbbbbbbbbbbbbbb'", "SELECT set_config('app.org_id', 'bbbbbbbbbbbbbbbbbbbbbbbb', true)", "SELECT set_config('role', 'pipeshub_jobs', true)"]) {
          const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, (tx) => tx.query(Q('t.rebind', sql)))).catch((x) => x)
          expect(e, sql).to.be.instanceOf(UowUsageError)
        }
        const next = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async (tx) => (await tx.query<{ o: string }>(Q('t.o', "SELECT current_setting('app.org_id') AS o"))).rows[0]!.o))
        expect(next).to.equal('aaaaaaaaaaaaaaaaaaaaaaaa')
      })

      it('prologue failure: the callable never runs, no user statement is sent, and the connection stays usable', async () => {
        const stmts: string[] = []
        const px = new Proxy(cluster.port, (c) => { stmts.push(c); return 'forward' })
        await px.start()
        try {
          const { rt } = rtOn(px.port)
          let ran = 0
          const e: any = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ service: 'connectors' }, async (tx) => { ran++; await tx.query(Q('t.x', 'SELECT 1')) })).catch((x) => x)
          expect(e).to.be.instanceOf(PrologueFailed)
          expect(ran).to.equal(0)
          expect(stmts.filter((s) => /t\.x/.test(s))).to.have.length(0)
          const ok = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, async (tx) => (await tx.query(Q('t.ok', 'SELECT 1 AS one'))).rows[0]))
          expect(ok).to.deep.equal({ one: 1 })
        } finally { px.stop() }
      })

      it('role-leak guard: 20 of 20 leaks detected, callable never runs, then recovers', async () => {
        const { rt } = rtOn(plain.port, {}, 4)
        const raw = new Client({ connectionString: cluster.url('fm_app', 'pipeshub_node_login', plain.port) })
        await raw.connect()
        try {
          let detected = 0
          let ran = 0
          for (let i = 0; i < 20; i++) {
            await raw.query('SET ROLE pipeshub_node')
            const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({ write: true }, async (tx) => { ran++; await tx.query(Q('t.x', 'SELECT 1')) })).catch((x) => x)
            if (e instanceof SessionRoleLeak) detected++
            await raw.query('RESET ROLE')
          }
          expect(detected).to.equal(20)
          expect(ran).to.equal(0)
          await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, (tx) => tx.query(Q('t.ok', 'SELECT 1'))))
        } finally { await raw.end() }
      })

      for (const [label, port] of [['reset', () => reset.port], ['plain (max_prepared_statements=0)', () => plain.port]] as const) {
        it(`unnamed statements: no prepared statement is ever visible on the server session (${label}), 1500 units`, async () => {
          const { rt } = rtOn(port(), {}, 6)
          let named = 0
          await inFlight(1500, 20, (i) => inOrg(ORGS[i % 5]!, () => rt.withUow({}, async (tx) => {
            await tx.query(Q('t.p', 'SELECT $1::int AS n'), [i])
            const ps = await tx.query<{ n: string }>(Q('t.ps', 'SELECT name FROM pg_prepared_statements'))
            named += ps.rows.length
          })))
          expect(named).to.equal(0)
        })
      }

      it('named statement refused outside customPlan, and behind azure_pgbouncer even in customPlan', async () => {
        const { rt } = rtOn(reset.port)
        const e = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => rt.withUow({}, (tx) => tx.query({ name: 'n1', text: Q('t.n', 'SELECT 1') }))).catch((x) => x)
        expect(e).to.be.instanceOf(UowUsageError)
        const az = rtOn(reset.port, { pooler: 'azure_pgbouncer' }).rt
        const e2 = await inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => az.withUow({ customPlan: true }, (tx) => tx.query({ name: 'n1', text: Q('t.n', 'SELECT 1') }))).catch((x) => x)
        expect(e2).to.be.instanceOf(UowUsageError)
      })

      it('ALS across awaits, streams, timers, emitters, microtasks, immediates and spawnDetached; 0 org bleed (pool of 2 forces queuing)', async () => {
        const { rt } = rtOn(reset.port, {}, 2)
        let bleed = 0
        let rowsCross = 0
        const bad = (s: string) => { bleed++; if (bleed < 5) console.log('      BLEED', s) } // eslint-disable-line no-console
        const emitter = new EventEmitter()
        await inFlight(600, 60, (i) => {
          const org = ORGS[i % 5]!
          return inOrg(org, () => rt.withUow({ write: i % 4 === 0 }, async (tx) => {
            const chk = (where: string) => { try { if (currentOrgContext().orgId !== org) bad(`${where} saw ${currentOrgContext().orgId} want ${org}`) } catch { bad(`${where} lost context`) } }
            chk('start')
            await sleep(1 + (i % 4))
            chk('after timer')
            await new Promise<void>((r) => setImmediate(() => { chk('immediate'); r() }))
            await new Promise<void>((r) => queueMicrotask(() => { chk('microtask'); r() }))
            const h = () => chk('emitter')
            emitter.once(`ev${i}`, h); emitter.emit(`ev${i}`)
            await pipe(Readable.from([1, 2, 3]), new Writable({ objectMode: true, write(_c, _e, cb) { chk('stream'); setTimeout(cb, 1) } }))
            const r = await tx.query<{ org_id: string }>(Q('t.all', 'SELECT org_id FROM kt.docs'))
            rowsCross += r.rows.filter((x) => x.org_id !== org).length
            const s = await tx.query<{ o: string }>(Q('t.o', "SELECT current_setting('app.org_id') AS o"))
            if (s.rows[0]!.o !== org || tx.orgId !== org) bad('db binding')
            const lost = await spawnDetached(async () => { try { currentOrgContext(); return false } catch (e) { return e instanceof MissingOrgContext } })
            if (!lost) bad('spawnDetached kept the context')
            const refused = await spawnDetached(() => rt.withUow({}, async () => 1)).catch((e) => e)
            if (!(refused instanceof MissingOrgContext)) bad('spawnDetached unit not refused')
          }))
        })
        expect(bleed).to.equal(0)
        expect(rowsCross).to.equal(0)
      })

      it('emitter registered under org A and fired from org B: a unit started in the listener uses B (the firing context)', async () => {
        const { rt } = rtOn(reset.port)
        const em = new EventEmitter()
        const seen: string[] = []
        inOrg('aaaaaaaaaaaaaaaaaaaaaaaa', () => em.on('go', () => { void rt.withUow({}, async (tx) => { seen.push(tx.orgId) }) }))
        await inOrg('bbbbbbbbbbbbbbbbbbbbbbbb', async () => { em.emit('go'); await sleep(100) })
        expect(seen).to.deep.equal(['bbbbbbbbbbbbbbbbbbbbbbbb'])
      })

      it('0 cross-org rows: 4 pools, 32 in flight, 4000 mixed reads/writes/aborts/timeouts/hostile ids, in-unit re-bind attempts', async () => {
        const rts = [0, 1, 2, 3].map(() => rtOn(reset.port, {}, 8).rt)
        let cross = 0
        let wrong = 0
        const kinds: Record<string, number> = {}
        const wrote: Record<string, number> = {}
        await inFlight(4000, 32, async (i) => {
          const org = ORGS[i % 5]!
          const rt = rts[i % 4]!
          const mode = i % 10
          const ctx = makeOrgContext({ orgId: org, userId: `u-${org}`, kind: 'user', source: 'jwt', requestId: mode === 9 ? `evil'; SET ROLE x; --${'z'.repeat(5000)}` : `r-${i}` })
          await runWithOrgContext(ctx, () => rt.withUow({ write: mode < 3, timeoutMs: mode === 8 ? 30 : undefined }, async (tx) => {
            if (mode === 4) await tx.query(Q('t.rebind', "SELECT set_config('app.org_id', 'org-evil', true), set_config('app.user_id', 'evil', true)"))
            if (mode < 3) {
              await tx.query(Q('t.ins', 'INSERT INTO kt.docs (org_id, id) VALUES ($1, $2)'), [org, `t-${i}`])
              wrote[org] = (wrote[org] ?? 0) + 1
            }
            if (mode === 8) await tx.query(Q('t.slow', 'SELECT pg_sleep(0.3)'))
            const r = await tx.query<{ org_id: string }>(Q('t.all', 'SELECT org_id FROM kt.docs'))
            cross += r.rows.filter((x) => x.org_id !== org).length
            if (mode === 4) return
            const s = await tx.query<{ o: string; u: string }>(Q('t.s', "SELECT current_setting('app.org_id') AS o, current_setting('app.user_id') AS u"))
            if (s.rows[0]!.o !== org || s.rows[0]!.u !== `u-${org}`) wrong++
            if (mode === 7) throw new Error('boom')
          })).catch((e) => { kinds[e.constructor.name] = (kinds[e.constructor.name] ?? 0) + 1 })
        })
        expect(cross).to.equal(0)
        expect(wrong).to.equal(0)
        expect(Object.keys(kinds).sort()).to.deep.equal(['DbTimeout', 'Error', 'UowUsageError'])
        for (const o of ORGS) {
          const n = Number((await val("SELECT count(*) AS n FROM kt.docs WHERE org_id = $1 AND id LIKE 't-%'", [o]))[0].n)
          expect(n, o).to.equal(wrote[o] ?? 0)
        }
      })
    })
  })
}
