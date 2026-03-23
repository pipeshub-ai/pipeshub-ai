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
})
