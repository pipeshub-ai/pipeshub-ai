import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as runtimeBoot from '../../../src/libs/db/runtime-boot'
import { PostgresCacheService } from '../../../src/libs/services/cache/postgresCache.service'
import { RedisCacheError } from '../../../src/libs/errors/redis.errors'

/**
 * PostgresCacheService without a database: the statements it sends, the keys and parameters, its errors and when it purges.
 * The same service against a real PostgreSQL is tests/libs/services/pg-cache.itest.ts.
 */
type Call = { sql: string; params: unknown[] }

function fakeDb(rows: (sql: string) => unknown[] = () => [], failPurge = false, purged = 0) {
  const calls: Call[] = []
  const savepoints: string[] = []
  const writes: boolean[] = []
  const tx = {
    query: sinon.stub().callsFake(async (sql: string, params: unknown[] = []) => {
      calls.push({ sql, params })
      if (failPurge && sql.includes('cache.purge')) throw new Error('canceling statement due to statement timeout')
      return { rows: rows(sql), rowCount: sql.includes('cache.purge') ? purged : 0 }
    }),
    savepoint: async (name: string, fn: () => Promise<unknown>) => {
      savepoints.push(name)
      return fn()
    },
  }
  sinon.stub(runtimeBoot, 'ensurePgRuntime').returns({
    withUow: (opts: { write: boolean }, fn: (t: unknown) => Promise<unknown>) => {
      writes.push(opts.write)
      return fn(tx)
    },
  } as unknown as ReturnType<typeof runtimeBoot.ensurePgRuntime>)
  const tags = () => calls.map((c) => /pipeshub:([a-z.]+)/.exec(c.sql)?.[1])
  return { calls, savepoints, writes, tags }
}

describe('PostgresCacheService', () => {
  let clock: sinon.SinonFakeTimers
  beforeEach(() => { clock = sinon.useFakeTimers({ now: 1_000_000, toFake: ['Date'] }) })
  afterEach(() => { clock.restore(); sinon.restore() })

  it('reads a live row by its prefixed, namespaced key and parses its JSON; a missing row is null', async () => {
    const db = fakeDb((sql) => (sql.includes('cache.get') ? [{ value: '{"userId":"u1"}' }] : []))
    const c = new PostgresCacheService('app:')
    expect(await c.get('s1', { namespace: 'session' })).to.deep.equal({ userId: 'u1' })
    expect(db.calls[0]!.params).to.deep.equal(['app:session:s1'])
    expect(db.calls[0]!.sql).to.contain('expires_at IS NULL OR expires_at > now()')
    expect(db.writes).to.deep.equal([false])

    sinon.restore()
    fakeDb()
    expect(await new PostgresCacheService().get('nothing')).to.equal(null)
  })

  it('writes JSON with an hour of expiry by default, or the ttl given', async () => {
    const db = fakeDb()
    const c = new PostgresCacheService('p:')
    await c.set('k', { a: 1 })
    await c.set('k', 'v', { ttl: 30, namespace: 'saml' })
    const sets = db.calls.filter((x) => x.sql.includes('cache.set'))
    expect(sets.map((x) => x.params)).to.deep.equal([['p:k', '{"a":1}', 3600], ['p:saml:k', '"v"', 30]])
  })

  it('deletes by key', async () => {
    const db = fakeDb()
    await new PostgresCacheService('p:').delete('k', { namespace: 'n' })
    expect(db.calls.find((x) => x.sql.includes('cache.delete'))!.params).to.deep.equal(['p:n:k'])
  })

  it('increments and returns the count, with no expiry unless a ttl is given', async () => {
    const db = fakeDb((sql) => (sql.includes('cache.incr') ? [{ value: '7' }] : []))
    const c = new PostgresCacheService('p:')
    expect(await c.increment('rate')).to.equal(7)
    expect(await c.increment('rate', { ttl: 60 })).to.equal(7)
    expect(db.calls.filter((x) => x.sql.includes('cache.incr')).map((x) => x.params)).to.deep.equal([['p:rate', null], ['p:rate', 60]])
  })

  it('reports a database failure as the cache error RedisService throws', async () => {
    sinon.stub(runtimeBoot, 'ensurePgRuntime').returns({
      withUow: () => Promise.reject(new Error('connection refused')),
    } as unknown as ReturnType<typeof runtimeBoot.ensurePgRuntime>)
    const c = new PostgresCacheService()
    for (const op of [() => c.get('k'), () => c.set('k', 1), () => c.delete('k'), () => c.increment('k')]) {
      const err = await op().catch((e: unknown) => e)
      expect(err).to.be.instanceOf(RedisCacheError)
    }
  })

  it('purges expired rows after a write at most once a minute, in a savepoint, skipping rows another replica holds', async () => {
    const db = fakeDb()
    const c = new PostgresCacheService()
    await c.get('k')                         // a read never purges
    await c.set('a', 1)
    await c.set('b', 2)
    expect(db.tags()).to.deep.equal(['cache.get', 'cache.set', 'cache.purge', 'cache.set'])
    expect(db.savepoints).to.deep.equal(['cache_purge'])
    expect(db.calls.find((x) => x.sql.includes('cache.purge'))!.sql).to.contain('FOR UPDATE SKIP LOCKED')
    clock.tick(61_000)
    await c.delete('a')
    expect(db.tags().filter((t) => t === 'cache.purge')).to.have.length(2)
  })

  it('keeps the write when the purge fails, and tries the purge again a few seconds later, not on every write', async () => {
    const db = fakeDb(() => [], true)
    const c = new PostgresCacheService()
    await c.set('a', 1)                      // the purge throws inside its savepoint; the write still succeeds
    await c.set('b', 2)
    expect(db.tags().filter((t) => t === 'cache.purge')).to.have.length(1)
    clock.tick(5_500)
    await c.set('c', 3)
    expect(db.tags().filter((t) => t === 'cache.purge')).to.have.length(2)
  })

  it('purges again a second later while each purge removes a full batch, so a backlog drains', async () => {
    const db = fakeDb(() => [], false, 1000)
    const c = new PostgresCacheService()
    await c.set('a', 1)
    expect(db.calls.find((x) => x.sql.includes('cache.purge'))!.params).to.deep.equal([1000])
    await c.set('b', 2)                      // the same second: no second batch in this write's moment
    clock.tick(1_100)
    await c.set('c', 3)
    expect(db.tags().filter((t) => t === 'cache.purge')).to.have.length(2)
    sinon.restore(); clock = sinon.useFakeTimers({ now: 1_000_000, toFake: ['Date'] })
    const quiet = fakeDb(() => [], false, 10)  // a partial batch: nothing waiting, back to once a minute
    const d = new PostgresCacheService()
    await d.set('a', 1)
    clock.tick(1_100)
    await d.set('b', 2)
    expect(quiet.tags().filter((t) => t === 'cache.purge')).to.have.length(1)
  })

  it('reports itself connected until disconnected, without closing the shared pool', async () => {
    const c = new PostgresCacheService()
    expect(c.isConnected()).to.equal(true)
    await c.disconnect()
    expect(c.isConnected()).to.equal(false)
  })
})
