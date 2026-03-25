import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { StorageContainer } from '../../../../src/modules/storage/container/storage.container'

describe('StorageContainer', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should be importable', () => {
    expect(StorageContainer).to.be.a('function')
  })

  describe('static methods', () => {
    it('should have initialize static method', () => {
      expect(StorageContainer.initialize).to.be.a('function')
    })

    it('should have getInstance static method', () => {
      expect(StorageContainer.getInstance).to.be.a('function')
    })

    it('should have dispose static method', () => {
      expect(StorageContainer.dispose).to.be.a('function')
    })
  })

  describe('getInstance', () => {
    it('should throw when container is not initialized', () => {
      const originalInstance = (StorageContainer as any).instance
      ;(StorageContainer as any).instance = null

      try {
        expect(() => StorageContainer.getInstance()).to.throw(
          'Service container not initialized',
        )
      } finally {
        ;(StorageContainer as any).instance = originalInstance
      }
    })

    it('should return the container when initialized', () => {
      const mockContainer = { isBound: sinon.stub() }
      const originalInstance = (StorageContainer as any).instance
      ;(StorageContainer as any).instance = mockContainer

      try {
        const result = StorageContainer.getInstance()
        expect(result).to.equal(mockContainer)
      } finally {
        ;(StorageContainer as any).instance = originalInstance
      }
    })
  })

  describe('initialize', () => {
    it('should accept configurationManagerConfig and appConfig parameters', () => {
      expect(StorageContainer.initialize.length).to.equal(2)
    })
  })

  describe('dispose', () => {
    it('should not throw when instance is null', async () => {
      const originalInstance = (StorageContainer as any).instance
      ;(StorageContainer as any).instance = null

      try {
        await StorageContainer.dispose()
      } finally {
        ;(StorageContainer as any).instance = originalInstance
      }
    })

    it('should disconnect KV store and set instance to null', async () => {
      const mockKvStore = { disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore
          return null
        }),
      }

      const originalInstance = (StorageContainer as any).instance
      ;(StorageContainer as any).instance = mockContainer

      try {
        await StorageContainer.dispose()
        expect((StorageContainer as any).instance).to.be.null
        expect(mockKvStore.disconnect.calledOnce).to.be.true
      } finally {
        if ((StorageContainer as any).instance !== null) {
          ;(StorageContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle missing KV store binding gracefully', async () => {
      const mockContainer = {
        isBound: sinon.stub().returns(false),
        get: sinon.stub(),
      }

      const originalInstance = (StorageContainer as any).instance
      ;(StorageContainer as any).instance = mockContainer

      try {
        await StorageContainer.dispose()
        expect((StorageContainer as any).instance).to.be.null
      } finally {
        if ((StorageContainer as any).instance !== null) {
          ;(StorageContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle KV store without disconnect method', async () => {
      const mockKvStore = {} // no disconnect method

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().returns(mockKvStore),
      }

      const originalInstance = (StorageContainer as any).instance
      ;(StorageContainer as any).instance = mockContainer

      try {
        await StorageContainer.dispose()
        expect((StorageContainer as any).instance).to.be.null
      } finally {
        if ((StorageContainer as any).instance !== null) {
          ;(StorageContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle errors during disconnect gracefully', async () => {
      const mockKvStore = {
        disconnect: sinon.stub().rejects(new Error('KV disconnect failed')),
      }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().returns(mockKvStore),
      }

      const originalInstance = (StorageContainer as any).instance
      ;(StorageContainer as any).instance = mockContainer

      try {
        await StorageContainer.dispose()
        expect((StorageContainer as any).instance).to.be.null
      } finally {
        if ((StorageContainer as any).instance !== null) {
          ;(StorageContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle disconnect timeout gracefully', async () => {
      // The StorageContainer uses Promise.race with 2s timeout for disconnect
      const mockKvStore = {
        disconnect: sinon.stub().returns(new Promise(() => {})), // never resolves
      }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().returns(mockKvStore),
      }

      const originalInstance = (StorageContainer as any).instance
      ;(StorageContainer as any).instance = mockContainer

      try {
        // Should resolve within the timeout (2 seconds) due to Promise.race
        await StorageContainer.dispose()
        expect((StorageContainer as any).instance).to.be.null
      } finally {
        if ((StorageContainer as any).instance !== null) {
          ;(StorageContainer as any).instance = originalInstance
        }
      }
    })
  })
})
