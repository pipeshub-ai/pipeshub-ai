import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  InProcessCacheService, getSharedRedisService, usesInProcessCache,
} from '../../../src/libs/services/redis.service'
import { Logger } from '../../../src/libs/services/logger.service'

describe('InProcessCacheService', () => {
  let clock: sinon.SinonFakeTimers
  beforeEach(() => { clock = sinon.useFakeTimers() })
  afterEach(() => { clock.restore() })

  it('stores JSON values, per namespace, until their ttl', async () => {
    const c = new InProcessCacheService()
    await c.set('k', { a: 1 }, { ttl: 10 })
    await c.set('k', 'other', { ttl: 10, namespace: 'ns' })
    expect(await c.get('k')).to.deep.equal({ a: 1 })
    expect(await c.get('k', { namespace: 'ns' })).to.equal('other')
    clock.tick(9_999)
    expect(await c.get('k')).to.deep.equal({ a: 1 })
    clock.tick(2)
    expect(await c.get('k')).to.equal(null)
  })

  it('deletes, and counts with the expiry fixed when the counter is created', async () => {
    const c = new InProcessCacheService()
    await c.set('d', 1)
    await c.delete('d')
    expect(await c.get('d')).to.equal(null)
    expect(await c.increment('n', { ttl: 10 })).to.equal(1)
    clock.tick(6_000)
    expect(await c.increment('n', { ttl: 10 })).to.equal(2)
    clock.tick(5_000)                       // 11 s after creation: the window ended, a new one starts
    expect(await c.increment('n', { ttl: 10 })).to.equal(1)
  })

  it('a counter without a ttl does not expire, as INCR alone sets none in Redis', async () => {
    const c = new InProcessCacheService()
    expect(await c.increment('plain')).to.equal(1)
    clock.tick(365 * 24 * 3600 * 1000)
    expect(await c.increment('plain')).to.equal(2)
  })

  it('a counter created without a ttl takes the first ttl it is given, and then keeps that expiry', async () => {
    const c = new InProcessCacheService()
    expect(await c.increment('late')).to.equal(1)
    expect(await c.increment('late', { ttl: 10 })).to.equal(2)
    clock.tick(6_000)
    expect(await c.increment('late', { ttl: 10 })).to.equal(3)
    clock.tick(5_000)
    expect(await c.increment('late', { ttl: 10 })).to.equal(1)
  })

  it('scans for expired entries at most once a minute, not on every write', async () => {
    const c = new InProcessCacheService()
    const size = () => (c as unknown as { entries: Map<string, unknown> }).entries.size
    for (let i = 0; i < 1100; i++) await c.set(`a${i}`, i, { ttl: 1 })
    clock.tick(61_000)
    await c.set('trigger', 1)                       // a minute after the last scan: the 1,100 expired entries go
    expect(size()).to.equal(1)
    for (let i = 0; i < 1100; i++) await c.set(`b${i}`, i, { ttl: 1 })
    clock.tick(2_000)
    await c.set('again', 1)                         // within the minute: no scan, the expired ones are still held
    expect(size()).to.be.greaterThan(1000)
    clock.tick(61_000)
    await c.set('later', 1)
    expect(size()).to.be.lessThan(10)
  })

  it('is chosen when no Redis is configured', () => {
    const saved = { ...process.env }
    try {
      delete process.env.REDIS_MODE
      delete process.env.KV_STORE_TYPE
      delete process.env.MESSAGE_BROKER
      expect(usesInProcessCache()).to.equal(false)
      process.env.KV_STORE_TYPE = 'postgres'
      expect(usesInProcessCache()).to.equal(false)
      process.env.MESSAGE_BROKER = 'postgres'
      expect(usesInProcessCache()).to.equal(true)
      process.env.REDIS_MODE = 'standalone'
      expect(usesInProcessCache()).to.equal(false)
      process.env.REDIS_MODE = 'postgres'                       // the mode the Python side defaults to: no Redis server either
      expect(usesInProcessCache()).to.equal(true)
      process.env.REDIS_MODE = 'inprocess'
      const svc = getSharedRedisService({ host: 'x', port: 1 }, Logger.getInstance({ service: 't' }))
      expect(svc).to.be.instanceOf(InProcessCacheService)
      expect(getSharedRedisService({ host: 'y', port: 2 }, Logger.getInstance({ service: 't' }))).to.equal(svc)
    } finally {
      for (const k of ['REDIS_MODE', 'KV_STORE_TYPE', 'MESSAGE_BROKER']) {
        if (saved[k] === undefined) delete process.env[k]
        else process.env[k] = saved[k]
      }
    }
  })
})
