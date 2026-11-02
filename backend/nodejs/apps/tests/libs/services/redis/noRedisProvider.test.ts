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
