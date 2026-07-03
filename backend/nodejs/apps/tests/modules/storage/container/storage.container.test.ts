import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { StorageContainer } from '../../../../src/modules/storage/container/storage.container'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'

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

describe('StorageContainer - coverage', () => {
  let originalInstance: any

  beforeEach(() => {
    originalInstance = (StorageContainer as any).instance
  })

  afterEach(() => {
    (StorageContainer as any).instance = originalInstance
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create container with all service bindings', async () => {
      const mockKvStore = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      const appConfig = {
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        storage: { type: 'local', config: {} },
      } as any

      const container = await StorageContainer.initialize(cmConfig as any, appConfig)

      expect(container).to.exist
      expect(container.isBound('ConfigurationManagerConfig')).to.be.true
      expect(container.isBound('KeyValueStoreService')).to.be.true
      expect(container.isBound('AuthMiddleware')).to.be.true
      expect(container.isBound('StorageConfig')).to.be.true
      expect(container.isBound('StorageController')).to.be.true

      const instance = StorageContainer.getInstance()
      expect(instance).to.equal(container)

      ;(StorageContainer as any).instance = null
    })

    it('should throw when KeyValueStoreService connect fails', async () => {
      const mockKvStore = {
        connect: sinon.stub().rejects(new Error('KV connect failed')),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

      const cmConfig = {
        host: 'localhost',
        port: 2379,
        storeType: 'etcd' as const,
        algorithm: 'aes-256-cbc',
        secretKey: 'test-secret-key-32-chars-long!!',
      }

      const appConfig = {
        jwtSecret: 'test-jwt-secret',
        scopedJwtSecret: 'test-scoped-jwt-secret',
        storage: { type: 'local', config: {} },
      } as any

      try {
        await StorageContainer.initialize(cmConfig as any, appConfig)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('KV connect failed')
      }
    })
  })

  describe('dispose - additional coverage', () => {
    it('should disconnect KeyValueStoreService with timeout', async () => {
      const mockKvStore = { disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().returns(mockKvStore),
      }

      ;(StorageContainer as any).instance = mockContainer
      await StorageContainer.dispose()

      expect(mockKvStore.disconnect.calledOnce).to.be.true
      expect((StorageContainer as any).instance).to.be.null
    })

    it('should handle disconnect error gracefully', async () => {
      const mockKvStore = { disconnect: sinon.stub().rejects(new Error('Disconnect error')) }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().returns(mockKvStore),
      }

      ;(StorageContainer as any).instance = mockContainer
      await StorageContainer.dispose()

      expect((StorageContainer as any).instance).to.be.null
    })

    it('should skip disconnect when KeyValueStoreService is not bound', async () => {
      const mockContainer = {
        isBound: sinon.stub().returns(false),
        get: sinon.stub(),
      }

      ;(StorageContainer as any).instance = mockContainer
      await StorageContainer.dispose()

      expect(mockContainer.get.called).to.be.false
      expect((StorageContainer as any).instance).to.be.null
    })

    it('should handle instance already disposed', async () => {
      ;(StorageContainer as any).instance = null
      await StorageContainer.dispose()
      // Should not throw
    })

    it('should skip disconnect when disconnect is not a function', async () => {
      const mockKvStore = { disconnect: 'not-a-function' }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().returns(mockKvStore),
      }

      ;(StorageContainer as any).instance = mockContainer
      await StorageContainer.dispose()

      expect((StorageContainer as any).instance).to.be.null
    })
  })
})
