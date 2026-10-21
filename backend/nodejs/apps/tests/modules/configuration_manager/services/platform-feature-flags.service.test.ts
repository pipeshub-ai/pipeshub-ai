import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as cmConfig from '../../../../src/modules/configuration_manager/config/config'
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor'
import { FixedClock } from '../../../../src/libs/types/clock'
import { PlatformFeatureFlags } from '../../../../src/modules/configuration_manager/services/platform-feature-flags.service'

/** Two flags that default to off. */
const FLAG_KEYS = { stored: 'ENABLE_MCP', unset: 'ENABLE_PROJECTS' } as const

describe('PlatformFeatureFlags', () => {
  let kvGet: sinon.SinonStub
  let clock: FixedClock
  let flags: PlatformFeatureFlags

  const store = (featureFlags: Record<string, boolean>) =>
    JSON.stringify({ featureFlags })

  beforeEach(() => {
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({
      algorithm: 'aes-256-gcm',
      secretKey: 'k',
    } as any)
    const decrypt = sinon.stub().callsFake((v: string) => v)
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns({ decrypt } as any)
    kvGet = sinon.stub().resolves(store({ [FLAG_KEYS.stored]: true }))
    clock = new FixedClock(1_000)
    flags = new PlatformFeatureFlags({ get: kvGet } as any, clock)
  })

  afterEach(() => sinon.restore())

  it('reads KV once for two calls within the TTL', async () => {
    expect(await flags.isEnabled(FLAG_KEYS.stored)).to.equal(true)
    clock.advance(9_999)
    expect(await flags.isEnabled(FLAG_KEYS.stored)).to.equal(true)
    expect(kvGet.callCount).to.equal(1)
  })

  it('re-reads after the TTL elapses', async () => {
    await flags.isEnabled(FLAG_KEYS.stored)
    clock.advance(10_000)
    kvGet.resolves(store({ [FLAG_KEYS.stored]: false }))
    expect(await flags.isEnabled(FLAG_KEYS.stored)).to.equal(false)
    expect(kvGet.callCount).to.equal(2)
  })

  it('collapses concurrent calls into one read', async () => {
    const results = await Promise.all([
      flags.isEnabled(FLAG_KEYS.stored),
      flags.isEnabled(FLAG_KEYS.stored),
      flags.isEnabled(FLAG_KEYS.unset),
    ])
    expect(results).to.deep.equal([true, true, false])
    expect(kvGet.callCount).to.equal(1)
  })

  it('serves the last good value when KV throws after the TTL', async () => {
    await flags.isEnabled(FLAG_KEYS.stored)
    clock.advance(10_000)
    kvGet.rejects(new Error('kv down'))
    expect(await flags.isEnabled(FLAG_KEYS.stored)).to.equal(true)
  })

  it('gives up on a hung read after 250 ms and keeps the last good value until the TTL passes', async () => {
    await flags.isEnabled(FLAG_KEYS.stored)
    clock.advance(10_000)
    kvGet.returns(new Promise(() => undefined)) // Redis paused: the read never settles
    const started = Date.now()
    expect(await flags.isEnabled(FLAG_KEYS.stored)).to.equal(true)
    expect(Date.now() - started).to.be.lessThan(1_000)
    expect(await flags.isEnabled(FLAG_KEYS.stored)).to.equal(true)
    expect(kvGet.callCount, 'not read again within the TTL').to.equal(2)
  })

  it('falls back to the default false when KV throws and nothing is cached', async () => {
    kvGet.rejects(new Error('kv down'))
    expect(await flags.isEnabled(FLAG_KEYS.stored)).to.equal(false)
  })

  it('returns false for an unknown key', async () => {
    expect(await flags.isEnabled('NOT_A_FLAG')).to.equal(false)
  })

  it('defaults a flag to false when the store has no entry', async () => {
    kvGet.resolves(null)
    expect(await flags.isEnabled(FLAG_KEYS.unset)).to.equal(false)
  })

  it('carries no container metadata: it is constructed directly and its clock and TTL have no bindings', () => {
    const keys = Reflect.getMetadataKeys(PlatformFeatureFlags).map(String)
    expect(keys.filter((k) => k.startsWith('inversify'))).to.deep.equal([])
  })
})
