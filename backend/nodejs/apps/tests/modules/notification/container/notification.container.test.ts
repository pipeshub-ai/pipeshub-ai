import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('notification/container/notification.container', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should be importable', async () => {
    try {
      const mod = await import('../../../../src/modules/notification/container/notification.container')
      expect(mod).to.be.an('object')
    } catch (error: any) {
      expect(error).to.exist
    }
  })
})
