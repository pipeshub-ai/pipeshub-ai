import 'reflect-metadata'
import { expect } from 'chai'
import { getRedisProvider, defaultRedisMode, resetRedisProviderRegistry } from '../../../../src/libs/services/redis/connectionProviderFactory'
import { NoRedisProvider } from '../../../../src/libs/services/redis/noRedisProvider'

describe('REDIS_MODE=postgres / inprocess in the provider factory', () => {
  const keys = ['REDIS_MODE', 'KV_STORE_TYPE', 'MESSAGE_BROKER']
  let saved: Record<string, string | undefined>
  beforeEach(() => { saved = Object.fromEntries(keys.map((k) => [k, process.env[k]])); keys.forEach((k) => delete process.env[k]); resetRedisProviderRegistry() })
  afterEach(() => { keys.forEach((k) => (saved[k] === undefined ? delete process.env[k] : (process.env[k] = saved[k]))); resetRedisProviderRegistry() })

  it('builds a provider for each mode instead of throwing "Unknown REDIS_MODE"', () => {
    for (const mode of ['postgres', 'inprocess']) {
      process.env.REDIS_MODE = mode
      const p = getRedisProvider()
      expect(p).to.be.instanceOf(NoRedisProvider)
      expect(p.mode).to.equal(mode)
    }
  })

  it('refuses a connection with a message that says why, rather than retrying against a host that is not there', async () => {
    process.env.REDIS_MODE = 'postgres'
    const p = getRedisProvider()
    expect(() => p.createClient()).to.throw(/without a Redis server/)
    expect(() => p.getClient()).to.throw(/REDIS_MODE=postgres/)
    expect(await p.ping()).to.equal(false)
    await p.close()
  })

  it('refuses every way to reach a server by name, and answers the rest without one', async () => {
    process.env.REDIS_MODE = 'inprocess'
    const p = getRedisProvider()
    expect(p.isCluster).to.equal(false)
    // a feature that still reaches for Redis is told which one it asked for, and how to get it
    expect(() => p.createPubSubClient()).to.throw(/REDIS_MODE=inprocess runs without a Redis server: Redis pub\/sub is not available/)
    expect(() => p.scanKeys('app:*')).to.throw(/a keyspace scan is not available/)
    expect(() => p.connectionUrl()).to.throw(/a Redis URL is not available.*REDIS_MODE=standalone \(or cluster\)/)
    // a script load is asynchronous: it rejects, it does not throw where it is called
    const load = p.loadScript('return 1')
    expect(load).to.be.instanceOf(Promise)
    const err = await load.catch((e: unknown) => e)
    expect(err).to.be.instanceOf(Error)
    expect((err as Error).message).to.equal('REDIS_MODE=inprocess runs without a Redis server')
    // what needs no server works: one slot, a prepare that does nothing, nothing to release
    expect(p.keySlot('{a}:b')).to.equal(0)
    expect(p.keySlot('other')).to.equal(0)
    await p.prepare()
    expect(() => p.release({} as never)).to.not.throw()
  })

  it('keeps the configured key namespace', () => {
    const p = new NoRedisProvider({ keyNamespace: 'tenant-a' } as never, 'postgres')
    expect(p.keyNamespace).to.equal('tenant-a')
    expect(p.mode).to.equal('postgres')
  })

  it('chooses the mode: the setting, else postgres when both stores are PostgreSQL, else standalone', () => {
    expect(defaultRedisMode()).to.equal('standalone')
    process.env.KV_STORE_TYPE = 'postgres'
    expect(defaultRedisMode()).to.equal('standalone')
    process.env.MESSAGE_BROKER = 'postgres'
    expect(defaultRedisMode()).to.equal('postgres')
    process.env.REDIS_MODE = 'cluster'
    expect(defaultRedisMode()).to.equal('cluster')
  })
})
