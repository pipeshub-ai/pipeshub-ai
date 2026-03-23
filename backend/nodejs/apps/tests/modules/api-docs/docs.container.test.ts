import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ApiDocsContainer } from '../../../src/modules/api-docs/docs.container'

describe('api-docs/docs.container', () => {
  afterEach(async () => {
    await ApiDocsContainer.dispose()
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create and return a container', async () => {
      const container = await ApiDocsContainer.initialize()
      expect(container).to.exist
    })

    it('should return same container on second call', async () => {
      const container1 = await ApiDocsContainer.initialize()
      const container2 = await ApiDocsContainer.initialize()
      expect(container1).to.equal(container2)
    })
  })

  describe('getContainer', () => {
    it('should throw if not initialized', () => {
      expect(() => ApiDocsContainer.getContainer()).to.throw('ApiDocsContainer not initialized')
    })

    it('should return container after initialization', async () => {
      await ApiDocsContainer.initialize()
      const container = ApiDocsContainer.getContainer()
      expect(container).to.exist
    })
  })

  describe('dispose', () => {
    it('should not throw when not initialized', async () => {
      await ApiDocsContainer.dispose()
    })

    it('should set container to null after dispose', async () => {
      await ApiDocsContainer.initialize()
      await ApiDocsContainer.dispose()
      expect(() => ApiDocsContainer.getContainer()).to.throw('ApiDocsContainer not initialized')
    })
  })
})
