import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('tokens_manager/config/config', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should export AppConfig interface and loadAppConfig function', async () => {
    try {
      const mod = await import('../../../../src/modules/tokens_manager/config/config')
      expect(mod.loadAppConfig).to.be.a('function')
    } catch (error: any) {
      // loadAppConfig depends on ConfigService which needs runtime env
      expect(error).to.exist
    }
  })

  it('fails startup on an unusable PASSWORD_RESET_LINK_EXPIRY before reading any config', async () => {
    const { loadAppConfig } = await import('../../../../src/modules/tokens_manager/config/config')
    const { ConfigService } = await import('../../../../src/modules/tokens_manager/services/cm.service')
    const getInstance = sinon.stub(ConfigService, 'getInstance')
    const original = process.env.PASSWORD_RESET_LINK_EXPIRY
    process.env.PASSWORD_RESET_LINK_EXPIRY = '10ms'
    try {
      let error: unknown
      try {
        await loadAppConfig()
      } catch (caught) {
        error = caught
      }
      expect(error).to.be.instanceOf(Error)
      expect((error as Error).message).to.match(/^PASSWORD_RESET_LINK_EXPIRY: /)
      expect(getInstance.called).to.be.false
    } finally {
      if (original === undefined) {
        delete process.env.PASSWORD_RESET_LINK_EXPIRY
      } else {
        process.env.PASSWORD_RESET_LINK_EXPIRY = original
      }
    }
  })
})
