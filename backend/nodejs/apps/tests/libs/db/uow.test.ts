/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { FakePool, pgError } from './fake-pool'
import {
  ConnectionLost, DbFenced, DbReadOnly, DbTimeout, DeadlockDetected, FailedTransaction, LockNotAvailable,
  PermissionDenied, PgRuntime, PoolTimeout, WriteIsolationRefused, PrologueFailed, QueryCatalog, SerializationFailure, SessionRoleLeak,
  UowUsageError, configurePg, mapPgError, registerLockKey, sanitizeRequestId, withUow,
} from '../../../src/libs/db'
import { UniqueConstraintError, ForeignKeyError } from '../../../src/libs/errors/database.errors'
import { lockKeyArgs } from '../../../src/libs/db/lock-keys'
import {
  MissingOrgContext, SystemContextNotAllowed, forOrg, makeOrgContext, runWithOrgContext, spawnDetached, system,
} from '../../../src/libs/context/orgContext'

const ctx = (orgId: string, requestId = 'req-1') =>
  makeOrgContext({ orgId, userId: `user-${orgId}`, kind: 'user', source: 'jwt', requestId })
const inOrg = <T>(orgId: string, fn: () => T) => runWithOrgContext(ctx(orgId), fn)
const noSleep = async () => {}
const rtFor = (pool: FakePool, extra: Partial<ConstructorParameters<typeof PgRuntime>[0]> = {}) =>
  new PgRuntime({ pool, defaultService: 'node', sleep: noSleep, ...extra })

describe('libs/db unit of work', () => {
  describe('org binding', () => {
    it('refuses with a typed error and sends no query when no org is bound', async () => {
      const pool = new FakePool()
      let ran = false
      const e = await rtFor(pool).withUow({}, async () => { ran = true }).catch((x) => x)
      expect(e).to.be.instanceOf(MissingOrgContext)
      expect(pool.connects).to.equal(0)
      expect(pool.calls).to.have.length(0)
      expect(ran).to.equal(false)
    })

    it('refuses a system context and the bootstrap org literal for an org unit', async () => {
      const pool = new FakePool()
      const rt = rtFor(pool)
      const a = await system('boot', () => rt.withUow({}, async () => 1)).catch((x) => x)
      expect(a).to.be.instanceOf(SystemContextNotAllowed)
      const b = await inOrg('system', () => rt.withUow({}, async () => 1)).catch((x) => x)
      expect(b).to.be.instanceOf(SystemContextNotAllowed)
      expect(pool.connects).to.equal(0)
    })

    it('binds the org, user and request id from the context, never from options', async () => {
      const pool = new FakePool()
      await inOrg('org-a', () => rtFor(pool).withUow({ service: 'node' }, async (tx) => {
        expect(tx.orgId).to.equal('org-a')
      }))
      const v = pool.prologue.values as unknown[]
      expect(v.slice(0, 4)).to.deep.equal(['pipeshub_node', 'org-a', 'req-1', 'user-org-a'])
    })

    it('preOrg runs with an empty org and needs no context', async () => {
      const pool = new FakePool()
      await rtFor(pool).withUow({ preOrg: true }, async () => 1)
      expect((pool.prologue.values as unknown[])[1]).to.equal('')
    })

    it('sanitises a hostile request id', async () => {
      const pool = new FakePool()
      const hostile = "x'; DROP TABLE t; --" + 'A'.repeat(5000)
      await runWithOrgContext(ctx('org-a', hostile), () => rtFor(pool).withUow({}, async () => 1))
      const rid = (pool.prologue.values as string[])[2]
      expect(rid).to.match(/^invalid-[0-9a-f]{12}$/)
      expect(sanitizeRequestId('abc-123.x:y')).to.equal('abc-123.x:y')
      expect(sanitizeRequestId(undefined)).to.match(/^invalid-/)
    })

    it('jobs units need a jobs pool; a system context runs with no org', async () => {
      const pool = new FakePool()
      const jobs = new FakePool()
      const rt = rtFor(pool, { jobsPool: jobs })
      await system('sweep', () => rt.withJobsUow({}, async () => 1))
      expect((jobs.prologue.values as unknown[]).slice(0, 2)).to.deep.equal(['pipeshub_jobs', ''])
      expect(pool.connects).to.equal(0)
      const e = await inOrg('o', () => rtFor(new FakePool()).withJobsUow({}, async () => 1)).catch((x) => x)
      expect(e).to.be.instanceOf(UowUsageError)
    })
  })

  describe('prologue', () => {
    it('is one statement; read units are READ ONLY, write units are not', async () => {
      const pool = new FakePool()
      const rt = rtFor(pool)
      await inOrg('o', () => rt.withUow({}, async () => 1))
      expect(pool.calls.map((c) => c.text.split('\n')[0])).to.deep.equal([
        'BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY', pool.prologue.text.split('\n')[0], 'COMMIT',
      ])
      const w = new FakePool()
      await inOrg('o', () => rtFor(w).withUow({ write: true }, async () => 1))
      expect(w.calls[0]?.text).to.equal('BEGIN ISOLATION LEVEL READ COMMITTED')
      const r = new FakePool()
      await inOrg('o', () => rtFor(r).withUow({ isolation: 'serializable' }, async () => 1))
      expect(r.calls[0]?.text).to.equal('BEGIN ISOLATION LEVEL SERIALIZABLE READ ONLY')
      for (const isolation of ['repeatable_read', 'serializable'] as const) {
        const p = new FakePool()
        for (const fence of [undefined, { fn: 'a.b', epoch: 1 }]) {
          const e = await inOrg('o', () => rtFor(p, { fence }).withUow({ write: true, isolation }, async () => 1)).catch((x) => x)
          expect(e).to.be.instanceOf(WriteIsolationRefused)
        }
        expect(p.connects).to.equal(0)
      }
    })

    it('sets timeouts by service class, lets options and org limits lower them only', async () => {
      const get = async (service: string, opts = {}, limits?: object) => {
        const pool = new FakePool()
        await inOrg('o', () => rtFor(pool, { orgLimits: () => limits }).withUow({ service, ...opts }, async () => 1))
        return (pool.prologue.values as string[]).slice(4, 7)
      }
      expect(await get('node')).to.deep.equal(['15000', '2000', 'auto'])
      expect(await get('query')).to.deep.equal(['5000', '2000', 'auto'])
      expect(await get('indexing')).to.deep.equal(['60000', '5000', 'auto'])
      expect(await get('node', { timeoutMs: 50, customPlan: true })).to.deep.equal(['50', '2000', 'force_custom_plan'])
      expect(await get('node', { timeoutMs: 20000 }, { timeoutMs: 1000, lockTimeoutMs: 99999 })).to.deep.equal(['1000', '2000', 'auto'])
    })

    it('passes the fence only to write units', async () => {
      const fence = { fn: 'kernel.assert_store_epoch', epoch: () => 7 }
      const r = new FakePool()
      await inOrg('o', () => rtFor(r, { fence }).withUow({}, async () => 1))
      expect((r.prologue.values as unknown[]).slice(8)).to.deep.equal([false, 7])
      const w = new FakePool()
      await inOrg('o', () => rtFor(w, { fence }).withUow({ write: true }, async () => 1))
      expect((w.prologue.values as unknown[]).slice(8)).to.deep.equal([true, 7])
      expect(w.prologue.text).to.contain('kernel.assert_store_epoch($10::int)')
      expect(() => rtFor(w, { fence: { fn: 'x; DROP TABLE y', epoch: 1 } })).to.throw(UowUsageError)
    })

    it('maps fence refusals: 25006 -> DbReadOnly, 55P04 -> DbFenced', async () => {
      for (const [code, cls] of [['25006', DbReadOnly], ['55P04', DbFenced]] as const) {
        const pool = new FakePool((t) => { if (t.startsWith('WITH pre')) throw pgError(code) ; return {} })
        const e = await inOrg('o', () => rtFor(pool).withUow({ write: true }, async () => 1)).catch((x) => x)
        expect(e).to.be.instanceOf(cls)
        expect(pool.calls.map((c) => c.text)).to.include('ROLLBACK')
      }
    })

    it('a failed prologue is a typed PrologueFailed and no caller statement runs', async () => {
      const pool = new FakePool((t) => { if (t.startsWith('WITH pre')) throw pgError('22023', 'role "pipeshub_nope" does not exist'); return {} })
      let ran = false
      const e = await inOrg('o', () => rtFor(pool).withUow({ service: 'nope' }, async (tx) => { ran = true; await tx.query('/* pipeshub:a.b */ SELECT 1') })).catch((x) => x)
      expect(e).to.be.instanceOf(PrologueFailed)
      expect((e as PrologueFailed).sqlstate).to.equal('22023')
      expect(ran).to.equal(false)
      expect(pool.userCalls).to.have.length(0)
      expect(pool.released).to.deep.equal([undefined])
    })

    it('a login that may not take the role is a PrologueFailed too', async () => {
      const pool = new FakePool((t) => { if (t.startsWith('WITH pre')) throw pgError('42501'); return {} })
      const e = await inOrg('o', () => rtFor(pool).withUow({}, async () => 1)).catch((x) => x)
      expect(e).to.be.instanceOf(PrologueFailed)
    })
  })

  describe('role-leak guard', () => {
    it('rolls back, destroys the connection, counts and raises; the callable never runs', async () => {
      const pool = new FakePool((t) => (t.startsWith('WITH pre') ? { rows: [{ r: 'pipeshub_node', fence: null }] } : {}))
      let ran = false
      const e = await inOrg('o', () => rtFor(pool).withUow({}, async () => { ran = true })).catch((x) => x)
      expect(e).to.be.instanceOf(SessionRoleLeak)
      expect(ran).to.equal(false)
      expect(pool.calls.map((c) => c.text)).to.include('ROLLBACK')
      expect(pool.released).to.deep.equal([true])
    })
  })

  describe('retries', () => {
    const body = (n: { v: number }) => async (tx: any) => { n.v++; return tx.query('/* pipeshub:a.b */ SELECT 1') }

    it('re-runs the whole callable on 40001 and 40P01 up to 3 times', async () => {
      for (const code of ['40001', '40P01']) {
        let failures = 2
        const pool = new FakePool((t) => { if (t.startsWith('/* pipeshub:a.b') && failures-- > 0) throw pgError(code); return t.startsWith('/* pipeshub:a.b') ? { rows: [{ ok: 1 }] } : {} })
        const n = { v: 0 }
        const r = await inOrg('o', () => rtFor(pool).withUow({ write: true }, body(n)))
        expect(r.rows).to.deep.equal([{ ok: 1 }])
        expect(n.v).to.equal(3)
        expect(pool.calls.filter((c) => c.text === 'ROLLBACK')).to.have.length(2)
      }
    })

    it('gives up after 3 retries with the typed error', async () => {
      const pool = new FakePool((t) => { if (t.startsWith('/* pipeshub:a.b')) throw pgError('40001'); return {} })
      const n = { v: 0 }
      const e = await inOrg('o', () => rtFor(pool).withUow({}, body(n))).catch((x) => x)
      expect(e).to.be.instanceOf(SerializationFailure)
      expect(n.v).to.equal(4)
    })

    it('does not retry a timeout or a lock timeout', async () => {
      for (const [code, cls] of [['57014', DbTimeout], ['55P03', LockNotAvailable]] as const) {
        const pool = new FakePool((t) => { if (t.startsWith('/* pipeshub:a.b')) throw pgError(code); return {} })
        const n = { v: 0 }
        const e = await inOrg('o', () => rtFor(pool).withUow({}, body(n))).catch((x) => x)
        expect(e).to.be.instanceOf(cls)
        expect(n.v).to.equal(1)
      }
    })

    it('retries a lost connection only before the first statement', async () => {
      let lose = 1
      const pool = new FakePool((t) => { if (t.startsWith('WITH pre') && lose-- > 0) throw pgError('57P01', 'terminating connection'); return {} })
      const n = { v: 0 }
      await inOrg('o', () => rtFor(pool).withUow({}, body(n)))
      expect(n.v).to.equal(1)
      expect(pool.released[0]).to.equal(true)

      const mid = new FakePool((t) => { if (t.startsWith('/* pipeshub:a.b')) throw new Error('Connection terminated unexpectedly'); return {} })
      const m = { v: 0 }
      const e = await inOrg('o', () => rtFor(mid).withUow({}, body(m))).catch((x) => x)
      expect(e).to.be.instanceOf(ConnectionLost)
      expect(m.v).to.equal(1)
    })

    it('retries a lost connection after statements when idempotent, but never once COMMIT was sent', async () => {
      let lose = 1
      const pool = new FakePool((t) => { if (t.startsWith('/* pipeshub:a.b') && lose-- > 0) throw new Error('Connection terminated unexpectedly'); return {} })
      const n = { v: 0 }
      await inOrg('o', () => rtFor(pool).withUow({ idempotent: true }, body(n)))
      expect(n.v).to.equal(2)

      const commit = new FakePool((t) => { if (t === 'COMMIT') throw new Error('Connection terminated unexpectedly'); return {} })
      const c = { v: 0 }
      const e = await inOrg('o', () => rtFor(commit).withUow({ idempotent: true }, body(c))).catch((x) => x)
      expect(e).to.be.instanceOf(ConnectionLost)
      expect((e as ConnectionLost).commitSent).to.equal(true)
      expect(c.v).to.equal(1)
    })

    it('destroys the connection when ROLLBACK itself fails', async () => {
      const pool = new FakePool((t) => { if (t === 'ROLLBACK') throw new Error('boom'); if (t.startsWith('/* pipeshub:a.b')) throw pgError('23505'); return {} })
      const e = await inOrg('o', () => rtFor(pool).withUow({}, body({ v: 0 }))).catch((x) => x)
      expect(e).to.be.instanceOf(UniqueConstraintError)
      expect(pool.released).to.deep.equal([true])
    })
  })

  describe('commit, savepoints, nesting, pool timeouts', () => {
    it('a COMMIT that answers ROLLBACK (aborted transaction, error swallowed) is never reported as success', async () => {
      const pool = new FakePool((t) => (t === 'COMMIT' ? { command: 'ROLLBACK' } : {}))
      const e = await inOrg('o', () => rtFor(pool).withUow({ write: true }, async () => 'ok')).catch((x) => x)
      expect(e).to.be.instanceOf(FailedTransaction)
    })

    describe('a transaction that ended early (COMMIT smuggled past the text guard)', () => {
      const probe = /commit_probe/
      const texts = (p: FakePool) => p.calls.map((c) => c.text)

      it('write units probe the transaction timestamp once, right before COMMIT; read units do not', async () => {
        const w = new FakePool()
        await inOrg('o', () => rtFor(w).withUow({ write: true }, async (tx) => { await tx.query('SELECT 1') }))
        const t = texts(w)
        expect(t.filter((x) => probe.test(x))).to.have.length(1)
        expect(t.at(-2)).to.match(probe)
        expect(t.at(-1)).to.equal('COMMIT')
        expect(w.prologue.text).to.contain('now()::text AS ts')
        const r = new FakePool()
        await inOrg('o', () => rtFor(r).withUow({}, async () => 1))
        expect(texts(r).some((x) => probe.test(x))).to.equal(false)
      })

      it('a changed transaction timestamp is FailedTransaction: no COMMIT, rolled back, connection discarded', async () => {
        const pool = new FakePool((t) => (probe.test(t) ? { rows: [{ ts: 'T1' }] } : {}))
        const e = await inOrg('o', () => rtFor(pool).withUow({ write: true }, async () => 'ok')).catch((x) => x)
        expect(e).to.be.instanceOf(FailedTransaction)
        expect(texts(pool)).to.not.include('COMMIT')
        expect(texts(pool).at(-1)).to.equal('ROLLBACK')
        expect(pool.released).to.deep.equal([true])
      })

      it('a probe that fails with "transaction aborted" (25P02) is FailedTransaction and the connection is discarded', async () => {
        const pool = new FakePool((t) => { if (probe.test(t)) throw pgError('25P02'); return {} })
        const e = await inOrg('o', () => rtFor(pool).withUow({ write: true }, async () => 'ok')).catch((x) => x)
        expect(e).to.be.instanceOf(FailedTransaction)
        expect(texts(pool)).to.not.include('COMMIT')
        expect(pool.released).to.deep.equal([true])
      })

      it('a probe that fails any other way keeps its own error', async () => {
        const pool = new FakePool((t) => { if (probe.test(t)) throw pgError('57014'); return {} })
        const e = await inOrg('o', () => rtFor(pool).withUow({ write: true }, async () => 'ok')).catch((x) => x)
        expect(e).to.be.instanceOf(DbTimeout)
      })

      it('an idle connection after a statement (the server left the transaction) stops the unit at once and is discarded', async () => {
        const pool = new FakePool()
        pool.status = () => (pool.calls.some((c) => c.text === 'SELECT 2') ? 'I' : 'T')
        let after = false
        const e = await inOrg('o', () => rtFor(pool).withUow({ write: true }, async (tx) => {
          await tx.query('SELECT 1')
          await tx.query('SELECT 2')
          after = true
        })).catch((x) => x)
        expect(e).to.be.instanceOf(FailedTransaction)
        expect(after).to.equal(false)
        expect(texts(pool)).to.not.include('COMMIT')
        expect(pool.released).to.deep.equal([true])
      })
    })

    it('savepoint: releases on success; rolls back to it and rethrows on failure so the unit can continue', async () => {
      const pool = new FakePool()
      await inOrg('o', () => rtFor(pool).withUow({ write: true }, async (tx) => {
        await tx.savepoint('s1', async () => 1)
        const e = await tx.savepoint('s2', async () => { throw new Error('dup') }).catch((x) => x)
        expect(e.message).to.equal('dup')
        expect(await tx.savepoint('s3', async () => 3)).to.equal(3)
        expect(await tx.savepoint('Bad Name', async () => 1).catch((x) => x)).to.be.instanceOf(UowUsageError)
      }))
      expect(pool.userCalls.map((c) => c.text)).to.deep.equal([
        'SAVEPOINT s1', 'RELEASE SAVEPOINT s1', 'SAVEPOINT s2', 'ROLLBACK TO SAVEPOINT s2', 'RELEASE SAVEPOINT s2',
        'SAVEPOINT s3', 'RELEASE SAVEPOINT s3',
      ])
    })

    it('refuses a unit started inside a unit, whatever the org, before taking a connection', async () => {
      const pool = new FakePool()
      const rt = rtFor(pool, { jobsPool: new FakePool() })
      const e = await inOrg('o', () => rt.withUow({}, async () => inOrg('o', () => rt.withUow({}, async () => 1)).catch((x) => x))) as unknown
      expect(e).to.be.instanceOf(UowUsageError)
      expect(pool.connects).to.equal(1)
      const j = await inOrg('o', () => rt.withUow({}, async () => rt.withJobsUow({}, async () => 1).catch((x) => x))) as unknown
      expect(j).to.be.instanceOf(UowUsageError)
      // after the unit has finished, a new one is fine
      expect(await inOrg('o', () => rt.withUow({}, async () => 'again'))).to.equal('again')
    })

    it('retries a pool timeout once for a read unit or an idempotent one, not for a plain write', async () => {
      const mk = () => { let n = 0; const p = new FakePool(); const c = p.connect.bind(p); p.connect = async () => { if (n++ === 0) throw new Error('timeout exceeded when trying to connect'); return c() }; return p }
      expect(await inOrg('o', () => rtFor(mk()).withUow({}, async () => 'ok'))).to.equal('ok')
      expect(await inOrg('o', () => rtFor(mk()).withUow({ write: true, idempotent: true }, async () => 'ok'))).to.equal('ok')
      const e = await inOrg('o', () => rtFor(mk()).withUow({ write: true }, async () => 'ok')).catch((x) => x)
      expect(e).to.be.instanceOf(PoolTimeout)
    })
  })

  describe('error mapping', () => {
    it('maps the documented SQLSTATEs', () => {
      const cls = (code: string) => mapPgError(pgError(code))
      expect(cls('25P02')).to.be.instanceOf(FailedTransaction)
      expect(cls('42501')).to.be.instanceOf(PermissionDenied)
      expect(cls('40P01')).to.be.instanceOf(DeadlockDetected)
      expect(mapPgError(Object.assign(pgError('23505'), { constraint: 'u_key' }))).to.be.instanceOf(UniqueConstraintError)
      expect(cls('23503')).to.be.instanceOf(ForeignKeyError)
      const plain = new Error('mine')
      expect(mapPgError(plain)).to.equal(plain)
    })
  })

  describe('statements', () => {
    it('refuses session SQL and transaction control at runtime', async () => {
      const pool = new FakePool()
      for (const sql of ['SET ROLE pipeshub_owner', 'SELECT set_config(\'a.b\', \'1\', false)', 'COMMIT', 'SELECT pg_advisory_lock(1)']) {
        const e = await inOrg('o', () => rtFor(pool).withUow({}, (tx) => tx.query(sql))).catch((x) => x)
        expect(e, sql).to.be.instanceOf(UowUsageError)
      }
      expect(pool.userCalls).to.have.length(0)
    })

    it('sends statements unnamed; named ones only in a customPlan unit behind a tracking pooler', async () => {
      const named = { name: 'q1', text: '/* pipeshub:a.b */ SELECT 1', values: [] }
      const pool = new FakePool()
      await inOrg('o', () => rtFor(pool).withUow({}, (tx) => tx.query('/* pipeshub:a.b */ SELECT $1', [1])))
      expect(pool.userCalls[0]?.name).to.equal(undefined)
      const e = await inOrg('o', () => rtFor(new FakePool()).withUow({}, (tx) => tx.query(named))).catch((x) => x)
      expect(e).to.be.instanceOf(UowUsageError)
      const ok = new FakePool()
      await inOrg('o', () => rtFor(ok).withUow({ customPlan: true }, (tx) => tx.query(named)))
      expect(ok.userCalls[0]?.name).to.equal('q1')
      const azure = await inOrg('o', () => rtFor(new FakePool(), { pooler: 'azure_pgbouncer' }).withUow({ customPlan: true }, (tx) => tx.query(named))).catch((x) => x)
      expect(azure).to.be.instanceOf(UowUsageError)
    })

    it('a handle kept past the unit is dead', async () => {
      let kept: any
      await inOrg('o', () => rtFor(new FakePool()).withUow({}, async (tx) => { kept = tx }))
      const e = await kept.query('/* pipeshub:a.b */ SELECT 1').catch((x: unknown) => x)
      expect(e).to.be.instanceOf(UowUsageError)
    })

    it('records tagged statements in the catalog and notes untagged ones', async () => {
      const catalog = new QueryCatalog()
      await inOrg('o', () => rtFor(new FakePool(), { catalog }).withUow({}, async (tx) => {
        await tx.query('/* pipeshub:identity.by_id */ SELECT 1 WHERE id = $1', ['x'])
        await tx.query('SELECT 2')
      }))
      expect(catalog.toJSON().statements).to.deep.equal([{ tag: 'identity.by_id', sql: '/* pipeshub:identity.by_id */ SELECT 1 WHERE id = $1', args: ['x'] }])
      expect([...catalog.untagged]).to.deep.equal(['SELECT 2'])
    })

    it('advisory locks use the transaction forms with a registered two-integer key', async () => {
      const key = registerLockKey('test.freeze', 7_340_101)
      expect(() => registerLockKey('test.other', 7_340_101)).to.throw(UowUsageError)
      expect(() => registerLockKey('test.bad', 0)).to.throw(UowUsageError)
      for (const c of [7340, 7341, 7350]) expect(() => registerLockKey(`test.kernel${c}`, c)).to.throw(UowUsageError)  // the kernel's own classes
      for (const n of ['docstore_fence', 'profile_fence', 'person_email']) expect(() => registerLockKey(n, 7_000_000)).to.throw(UowUsageError)
      expect(() => registerLockKey('profile_fence', 7341)).to.throw(UowUsageError)
      const forged = { name: 'forged', classId: 7341 }
      expect(() => lockKeyArgs(forged, 1)).to.throw(UowUsageError)
      const pool = new FakePool((t) => (t.includes('try_advisory') ? { rows: [{ ok: true }] } : {}))
      await inOrg('o', () => rtFor(pool).withUow({}, async (tx) => {
        await tx.advisoryXactLock(key, 5)
        expect(await tx.tryAdvisoryXactLock(key, 5, true)).to.equal(true)
        const stranger = await tx.advisoryXactLock({ name: 'unregistered', classId: 9 }, 1).catch((x) => x)
        expect(stranger).to.be.instanceOf(UowUsageError)
      }))
      expect(pool.userCalls[0]?.values).to.deep.equal([7_340_101, 5])
      expect(pool.userCalls[1]?.text).to.contain('pg_try_advisory_xact_lock_shared')
    })
  })

  describe('kysely', () => {
    it('runs builder queries through the unit with the tag, and refuses its own transactions', async () => {
      const pool = new FakePool((t) => (t.includes('"things"') ? { rows: [{ id: 'a' }], rowCount: 1 } : {}))
      const catalog = new QueryCatalog()
      await inOrg('o', () => rtFor(pool, { catalog }).withUow({}, async (tx) => {
        const db = tx.db<{ things: { id: string; org_id: string } }>('things.by_org')
        const rows = await db.selectFrom('things').select('id').where('org_id', '=', tx.orgId).execute()
        expect(rows).to.deep.equal([{ id: 'a' }])
        const e = await db.transaction().execute(async () => 1).catch((x) => x)
        expect(e).to.be.instanceOf(UowUsageError)
        expect(() => tx.db('Bad Tag')).to.throw(UowUsageError)
      }))
      const call = pool.userCalls[0]
      expect(call?.text).to.match(/^\/\* pipeshub:things\.by_org \*\/ select "id" from "things" where "org_id" = \$1$/)
      expect(call?.name).to.equal(undefined)
      expect(call?.values).to.deep.equal(['o'])
      expect(catalog.toJSON().statements[0]?.tag).to.equal('things.by_org')
    })
  })

  describe('opt-in', () => {
    it('the module-level withUow fails until a feature configures PG', async () => {
      configurePg(undefined)
      const e = await withUow({ preOrg: true }, async () => 1).catch((x) => x)
      expect(e).to.be.instanceOf(UowUsageError)
      configurePg(rtFor(new FakePool()))
      expect(await withUow({ preOrg: true }, async () => 1)).to.equal(1)
      configurePg(undefined)
    })
  })

  describe('async context', () => {
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

    it('keeps each request\'s org across awaits, timers and streams under concurrency', async () => {
      const seen: Array<[string, string, string, string]> = []
      const pool = new FakePool(async (t) => { if (t.startsWith('/* pipeshub:a.b')) await sleep(Math.random() * 5); return { rows: [] } })
      const rt = rtFor(pool)
      const { currentOrgContext } = await import('../../../src/libs/context/orgContext')
      await Promise.all(Array.from({ length: 200 }, (_, i) => {
        const org = `org-${i % 7}`
        return inOrg(org, () => rt.withUow({}, async (tx) => {
          await tx.query('/* pipeshub:a.b */ SELECT 1')
          const afterAwait = currentOrgContext().orgId as string
          const afterTimer = await new Promise<string>((res) => setTimeout(() => res(currentOrgContext().orgId as string), Math.random() * 5))
          let inStream = ''
          await pipeline(Readable.from(['x', 'y']), async function (src) { for await (const chunk of src) { if (chunk !== undefined) inStream = currentOrgContext().orgId as string } })
          seen.push([org, afterAwait, afterTimer, inStream])
          await tx.query('/* pipeshub:a.b */ SELECT 2')
          expect(tx.orgId).to.equal(org)
        }))
      }))
      expect(seen).to.have.length(200)
      for (const [org, a, t, s] of seen) expect([a, t, s]).to.deep.equal([org, org, org])
      const orgs = pool.calls.filter((c) => c.text.startsWith('WITH pre')).map((c) => (c.values as string[])[1])
      expect(new Set(orgs).size).to.equal(7)
    })

    it('work started inside spawnDetached has no org: a unit there is refused', async () => {
      const rt = rtFor(new FakePool())
      const detached = await inOrg('org-a', () => rt.withUow({}, async () =>
        new Promise<unknown>((res) => spawnDetached(() => setTimeout(() => { void rt.withUow({}, async () => 1).catch(res) }, 1)))))
      expect(detached).to.be.instanceOf(MissingOrgContext)
    })

    it('a job loop binds one org per iteration from its own enumeration', async () => {
      const pool = new FakePool()
      const rt = rtFor(pool)
      for (const o of ['o1', 'o2']) await forOrg(o, 'sweep', () => rt.withUow({}, async () => 1))
      expect(pool.calls.filter((c) => c.text.startsWith('WITH pre')).map((c) => (c.values as string[])[1])).to.deep.equal(['o1', 'o2'])
    })
  })
})
