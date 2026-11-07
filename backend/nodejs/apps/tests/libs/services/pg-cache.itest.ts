/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { randomBytes } from 'node:crypto'
import { PostgresCacheService } from '../../../src/libs/services/cache/postgresCache.service'
import { PostgresDistributedKeyValueStore } from '../../../src/libs/keyValueStore/providers/PostgresDistributedKeyValueStore'
import { RedisCacheError } from '../../../src/libs/errors/redis.errors'
import { configurePg } from '../../../src/libs/db'
import * as runtimeBoot from '../../../src/libs/db/runtime-boot'
import type { Tx } from '../../../src/libs/db/uow'

/**
 * The Node API's cache on a real PostgreSQL (`REDIS_MODE=postgres`): sessions, the SAML hand-off and the e-mail rate-limit
 * counters, with RedisService's semantics. PG_RUNTIME_TEST_URL is the node login of a database migrated to the heads, reached
 * directly (no pooler).
 */
const URL = process.env.PG_RUNTIME_TEST_URL ?? ''
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function expiresAt(key: string): Promise<Date | null> {
  const r = await runtimeBoot.ensurePgRuntime().withUow({ preOrg: true, write: false }, (tx) =>
    tx.query<{ expires_at: Date | null }>(
      '/* pipeshub:test.cache.expiry */ SELECT expires_at FROM platform.cache_entries WHERE key = $1', [key]))
  return r.rows[0]?.expires_at ?? null
}

(URL ? describe : describe.skip)('PostgreSQL cache (REDIS_MODE=postgres)', function () {
  this.timeout(60000)
  let p: string
  before(() => {
    process.env.PG_NODE_URL = URL
    process.env.PG_POOLER = 'none'
  })
  beforeEach(() => { p = `t${randomBytes(5).toString('hex')}:` })
  after(() => configurePg(undefined))

  it('stores JSON values per namespace, and another instance (another process, or after a restart) reads them', async () => {
    const a = new PostgresCacheService(p)
    await a.set('session:1', { userId: 'u1', orgId: 'o1', n: [1, 2] })
    await a.set('session:1', 'other', { namespace: 'saml' })
    const b = new PostgresCacheService(p)
    expect(await b.get('session:1')).to.deep.equal({ userId: 'u1', orgId: 'o1', n: [1, 2] })
    expect(await b.get('session:1', { namespace: 'saml' })).to.equal('other')
    expect(await b.get('missing')).to.equal(null)
    await b.delete('session:1')
    expect(await a.get('session:1')).to.equal(null)
    expect(await a.get('session:1', { namespace: 'saml' })).to.equal('other')
  })

  it('expires a value after its ttl, an hour by default, and a write replaces an expired row', async () => {
    const c = new PostgresCacheService(p)
    await c.set('short', 1, { ttl: 1 })
    await c.set('long', 2)
    const hour = (await expiresAt(`${p}long`))!.getTime() - Date.now()
    expect(hour).to.be.within(3590_000, 3600_000)
    await sleep(1200)
    expect(await c.get('short')).to.equal(null)
    await c.set('short', 3, { ttl: 60 })
    expect(await c.get('short')).to.equal(3)
  })

  it('increments like INCR, and a ttl is set again on every call like EXPIRE', async () => {
    const c = new PostgresCacheService(p)
    expect(await c.increment('rate')).to.equal(1)
    expect(await c.increment('rate')).to.equal(2)
    expect(await expiresAt(`${p}rate`)).to.equal(null)            // INCR alone sets no expiry
    expect(await c.increment('rate', { ttl: 60 })).to.equal(3)
    const first = (await expiresAt(`${p}rate`))!.getTime()
    await sleep(1100)
    expect(await c.increment('rate', { ttl: 60 })).to.equal(4)
    expect((await expiresAt(`${p}rate`))!.getTime()).to.be.greaterThan(first)   // refreshed
    expect(await c.increment('rate')).to.equal(5)
    expect(await expiresAt(`${p}rate`)).to.not.equal(null)        // without a ttl the expiry stays
    expect(await c.get('rate')).to.equal(5)
  })

  it('starts an expired counter again at 1', async () => {
    const c = new PostgresCacheService(p)
    await c.increment('win', { ttl: 1 })
    await c.increment('win', { ttl: 1 })
    await sleep(1200)
    expect(await c.increment('win')).to.equal(1)
    expect(await expiresAt(`${p}win`)).to.equal(null)
  })

  it('counts one address together across instances, as two API replicas on Redis do', async () => {
    const replicas = [new PostgresCacheService(p), new PostgresCacheService(p)]
    const got = await Promise.all(Array.from({ length: 20 }, (_, i) => replicas[i % 2]!.increment('otp:addr', { ttl: 60 })))
    expect([...got].sort((x, y) => x - y)).to.deep.equal(Array.from({ length: 20 }, (_, i) => i + 1))
  })

  it('fails with the cache error when a value is not an integer, as INCR does', async () => {
    const c = new PostgresCacheService(p)
    await c.set('word', 'abc')
    let err: unknown
    try { await c.increment('word') } catch (e) { err = e }
    expect(err).to.be.instanceOf(RedisCacheError)
  })

  it('commits the write when the purge after it fails in PostgreSQL', async () => {
    // The purge is replaced by a statement PostgreSQL rejects (division by zero), which aborts the transaction unless its
    // savepoint is rolled back: the session write before it must still commit.
    const real = runtimeBoot.ensurePgRuntime()
    const failing = {
      withUow: <T>(opts: Parameters<typeof real.withUow>[0], fn: (tx: Tx) => Promise<T>) =>
        real.withUow(opts, (tx) => fn(new Proxy(tx, {
          get(t, prop) {
            const v = Reflect.get(t, prop, t) as unknown
            if (prop !== 'query' || typeof v !== 'function') return typeof v === 'function' ? (v as (...a: unknown[]) => unknown).bind(t) : v
            return (sql: string, params?: unknown[]) =>
              sql.includes('pipeshub:cache.purge')
                ? t.query('/* pipeshub:test.cache.purgefail */ SELECT 1 / 0')
                : t.query(sql, params)
          },
        }))),
    }
    const stub = sinon.stub(runtimeBoot, 'ensurePgRuntime').returns(failing as unknown as ReturnType<typeof runtimeBoot.ensurePgRuntime>)
    try {
      const c = new PostgresCacheService(p)      // a new instance purges after its first write
      await c.set('kept', { s: 1 })     // throws if the failed purge rolled the write back
    } finally {
      stub.restore()
    }
    expect(await new PostgresCacheService(p).get('kept')).to.deep.equal({ s: 1 })
    await new PostgresCacheService(p).delete('kept')
  })

  it('keeps its entries out of the key-value store, so configuration listings never see a session', async () => {
    const c = new PostgresCacheService(p)
    await c.set('session:kv', { s: 1 })
    const kv = new PostgresDistributedKeyValueStore<unknown>((v) => Buffer.from(JSON.stringify(v)), (b) => JSON.parse(b.toString()))
    expect((await kv.getAllKeys()).filter((k) => k.startsWith(p))).to.deep.equal([])
    expect(await kv.getKey(`${p}session:kv`)).to.equal(null)
  })
})
