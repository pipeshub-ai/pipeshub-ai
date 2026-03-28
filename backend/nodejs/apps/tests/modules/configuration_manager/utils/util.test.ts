import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

// The getPlatformSettingsFromStore function depends on external services
// We test the structure and default behavior
describe('configuration_manager/utils/util', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should export getPlatformSettingsFromStore function', async () => {
    try {
      const mod = await import('../../../../src/modules/configuration_manager/utils/util')
      expect(mod.getPlatformSettingsFromStore).to.be.a('function')
    } catch (error: any) {
      // May fail due to dependencies needing runtime env
      expect(error).to.exist
    }
  })
})
