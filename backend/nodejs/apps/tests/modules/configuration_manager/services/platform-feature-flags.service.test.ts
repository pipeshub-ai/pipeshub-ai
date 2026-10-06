import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as cmConfig from '../../../../src/modules/configuration_manager/config/config'
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor'
import { FixedClock } from '../../../../src/libs/types/clock'
import {
  COLLAB_FLAG_KEYS,
  PlatformFeatureFlags,
} from '../../../../src/modules/configuration_manager/services/platform-feature-flags.service'

describe('PH02-15 PlatformFeatureFlags', () => {
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
    kvGet = sinon.stub().resolves(store({ [COLLAB_FLAG_KEYS.chatMentions]: true }))
    clock = new FixedClock(1_000)
    flags = new PlatformFeatureFlags({ get: kvGet } as any, clock)
  })

  afterEach(() => sinon.restore())

  it('reads KV once for two calls within the TTL', async () => {
    expect(await flags.isEnabled(COLLAB_FLAG_KEYS.chatMentions)).to.equal(true)
    clock.advance(9_999)
    expect(await flags.isEnabled(COLLAB_FLAG_KEYS.chatMentions)).to.equal(true)
    expect(kvGet.callCount).to.equal(1)
  })

  it('re-reads after the TTL elapses', async () => {
    await flags.isEnabled(COLLAB_FLAG_KEYS.chatMentions)
    clock.advance(10_000)
    kvGet.resolves(store({ [COLLAB_FLAG_KEYS.chatMentions]: false }))
    expect(await flags.isEnabled(COLLAB_FLAG_KEYS.chatMentions)).to.equal(false)
    expect(kvGet.callCount).to.equal(2)
  })

  it('collapses concurrent calls into one read', async () => {
    const results = await Promise.all([
      flags.isEnabled(COLLAB_FLAG_KEYS.chatMentions),
      flags.isEnabled(COLLAB_FLAG_KEYS.chatMentions),
      flags.isEnabled(COLLAB_FLAG_KEYS.collaborativeChats),
    ])
    expect(results).to.deep.equal([true, true, false])
    expect(kvGet.callCount).to.equal(1)
  })

  it('serves the last good value when KV throws after the TTL', async () => {
    await flags.isEnabled(COLLAB_FLAG_KEYS.chatMentions)
    clock.advance(10_000)
    kvGet.rejects(new Error('kv down'))
    expect(await flags.isEnabled(COLLAB_FLAG_KEYS.chatMentions)).to.equal(true)
  })

  it('falls back to the default false when KV throws and nothing is cached', async () => {
    kvGet.rejects(new Error('kv down'))
    expect(await flags.isEnabled(COLLAB_FLAG_KEYS.chatMentions)).to.equal(false)
  })

  it('returns false for an unknown key', async () => {
    expect(await flags.isEnabled('NOT_A_FLAG')).to.equal(false)
  })

  it('defaults a collab flag to false when the store has no entry', async () => {
    kvGet.resolves(null)
    expect(await flags.isEnabled(COLLAB_FLAG_KEYS.collaborativeChats)).to.equal(false)
  })
})
