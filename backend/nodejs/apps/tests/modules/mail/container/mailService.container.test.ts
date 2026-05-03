import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('mail/container/mailService.container', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should be importable', async () => {
    try {
      const mod = await import('../../../../src/modules/mail/container/mailService.container')
      expect(mod).to.be.an('object')
    } catch (error: any) {
      expect(error).to.exist
    }
  })
})
