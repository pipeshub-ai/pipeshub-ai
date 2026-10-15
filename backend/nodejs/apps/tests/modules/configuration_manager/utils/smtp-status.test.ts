import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as cmConfig from '../../../../src/modules/configuration_manager/config/config'
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor'
import {
  invalidateSmtpStatusCache,
  isSmtpConfigured,
} from '../../../../src/modules/configuration_manager/utils/smtp-status'
import { getSmtpConfigStatus } from '../../../../src/modules/configuration_manager/controller/cm_controller'

const VALID = { host: 'smtp.test.com', port: 587, fromEmail: 'a@test.com' }

describe('configuration_manager/utils/smtp-status', () => {
  let clock: sinon.SinonFakeTimers
  let decrypt: sinon.SinonStub

  const kvWith = (value: string | null) =>
    ({ get: sinon.stub().resolves(value) }) as any

  beforeEach(() => {
    clock = sinon.useFakeTimers(1_000_000)
    decrypt = sinon.stub()
    sinon
      .stub(cmConfig, 'loadConfigurationManagerConfig')
      .returns({ algorithm: 'aes-256-gcm', secretKey: 'k' } as any)
    sinon
      .stub(encryptorModule.EncryptionService, 'getInstance')
      .returns({ decrypt } as any)
    invalidateSmtpStatusCache()
  })

  afterEach(() => {
    clock.restore()
    sinon.restore()
    invalidateSmtpStatusCache()
  })

  it('is false when nothing is stored', async () => {
    expect(await isSmtpConfigured(kvWith(null))).to.equal(false)
  })

  it('is false when host, port or fromEmail is missing', async () => {
    decrypt.returns(JSON.stringify({ host: 'h', port: 25 }))
    expect(await isSmtpConfigured(kvWith('enc'))).to.equal(false)
  })

  it('is true for a complete config', async () => {
    decrypt.returns(JSON.stringify(VALID))
    expect(await isSmtpConfigured(kvWith('enc'))).to.equal(true)
  })

  it('caches for 60 s then re-reads', async () => {
    decrypt.returns(JSON.stringify(VALID))
    const kv = kvWith('enc')
    await isSmtpConfigured(kv)
    clock.tick(59_999)
    await isSmtpConfigured(kv)
    expect(kv.get.callCount).to.equal(1)
    clock.tick(1)
    await isSmtpConfigured(kv)
    expect(kv.get.callCount).to.equal(2)
  })

  it('invalidate forces a re-read', async () => {
    decrypt.returns(JSON.stringify(VALID))
    const kv = kvWith('enc')
    await isSmtpConfigured(kv)
    invalidateSmtpStatusCache()
    await isSmtpConfigured(kv)
    expect(kv.get.callCount).to.equal(2)
  })

  it('does not cache a failed read', async () => {
    const kv = { get: sinon.stub() } as any
    kv.get.onFirstCall().rejects(new Error('kv down'))
    kv.get.onSecondCall().resolves(null)
    let thrown: unknown
    try {
      await isSmtpConfigured(kv)
    } catch (e) {
      thrown = e
    }
    expect(thrown).to.be.instanceOf(Error)
    expect(await isSmtpConfigured(kv)).to.equal(false)
  })

  describe('getSmtpConfigStatus', () => {
    const run = async (kv: any) => {
      const res: any = {}
      res.status = sinon.stub().returns(res)
      res.json = sinon.stub().returns(res)
      res.end = sinon.stub().returns(res)
      const next = sinon.stub()
      await getSmtpConfigStatus(kv)({} as any, res, next)
      return { res, next }
    }

    it('responds { configured } from the shared helper', async () => {
      decrypt.returns(JSON.stringify(VALID))
      const { res } = await run(kvWith('enc'))
      expect(res.status.calledWith(200)).to.equal(true)
      expect(res.json.firstCall.args[0]).to.deep.equal({ configured: true })
    })

    it('forwards read errors to next', async () => {
      const kv = { get: sinon.stub().rejects(new Error('boom')) }
      const { next } = await run(kv)
      expect(next.calledOnce).to.equal(true)
    })
  })
})
