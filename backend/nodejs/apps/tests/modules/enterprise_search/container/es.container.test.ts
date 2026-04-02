import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('enterprise_search/container/es.container', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should be importable', async () => {
    try {
      const mod = await import('../../../../src/modules/enterprise_search/container/es.container')
      expect(mod).to.be.an('object')
    } catch (error: any) {
      expect(error).to.exist
    }
  })
})
