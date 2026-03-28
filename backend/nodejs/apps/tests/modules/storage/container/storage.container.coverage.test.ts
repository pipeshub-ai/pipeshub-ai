import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { StorageContainer } from '../../../../src/modules/storage/container/storage.container'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'

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
