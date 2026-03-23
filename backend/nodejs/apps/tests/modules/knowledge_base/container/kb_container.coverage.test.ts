import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { KnowledgeBaseContainer } from '../../../../src/modules/knowledge_base/container/kb_container'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import { RecordsEventProducer } from '../../../../src/modules/knowledge_base/services/records_events.service'
import { SyncEventProducer } from '../../../../src/modules/knowledge_base/services/sync_events.service'

describe('KnowledgeBaseContainer - coverage', () => {
  let originalInstance: any

  beforeEach(() => {
    originalInstance = (KnowledgeBaseContainer as any).instance
  })

  afterEach(() => {
    (KnowledgeBaseContainer as any).instance = originalInstance
    sinon.restore()
  })

  describe('initialize', () => {
    it('should create container with all bindings', async () => {
      const mockKvStore = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)
      sinon.stub(RecordsEventProducer.prototype, 'start').resolves()
      sinon.stub(SyncEventProducer.prototype, 'start').resolves()

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
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      } as any

      const container = await KnowledgeBaseContainer.initialize(cmConfig as any, appConfig)

      expect(container).to.exist
      expect(container.isBound('Logger')).to.be.true
      expect(container.isBound('ConfigurationManagerConfig')).to.be.true
      expect(container.isBound('AppConfig')).to.be.true
      expect(container.isBound('KeyValueStoreService')).to.be.true
      expect(container.isBound('RecordsEventProducer')).to.be.true
      expect(container.isBound('SyncEventProducer')).to.be.true
      expect(container.isBound('AuthMiddleware')).to.be.true

      const instance = KnowledgeBaseContainer.getInstance()
      expect(instance).to.equal(container)

      ;(KnowledgeBaseContainer as any).instance = null
    })

    it('should throw when KeyValueStoreService connect fails', async () => {
      const mockKvStore = {
        connect: sinon.stub().rejects(new Error('KV store unreachable')),
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
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      } as any

      try {
        await KnowledgeBaseContainer.initialize(cmConfig as any, appConfig)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('KV store unreachable')
      }
    })

    it('should throw when RecordsEventProducer.start fails', async () => {
      const mockKvStore = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        isConnected: sinon.stub().returns(true),
      }
      sinon.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)
      sinon.stub(RecordsEventProducer.prototype, 'start').rejects(new Error('Kafka unavailable'))

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
        kafka: { brokers: ['localhost:9092'], clientId: 'test' },
      } as any

      try {
        await KnowledgeBaseContainer.initialize(cmConfig as any, appConfig)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('Kafka unavailable')
      }
    })
  })

  describe('dispose - additional coverage', () => {
    it('should stop RecordsEventProducer and SyncEventProducer', async () => {
      const mockRecords = { stop: sinon.stub().resolves() }
      const mockSync = { stop: sinon.stub().resolves() }
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['RecordsEventProducer', 'SyncEventProducer', 'KeyValueStoreService'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'RecordsEventProducer') return mockRecords
          if (key === 'SyncEventProducer') return mockSync
          if (key === 'KeyValueStoreService') return mockKvStore
          return null
        }),
      }

      ;(KnowledgeBaseContainer as any).instance = mockContainer
      await KnowledgeBaseContainer.dispose()

      expect(mockRecords.stop.calledOnce).to.be.true
      expect(mockSync.stop.calledOnce).to.be.true
      expect(mockKvStore.disconnect.calledOnce).to.be.true
    })

    it('should skip KeyValueStoreService disconnect when not connected', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore
          return null
        }),
      }

      ;(KnowledgeBaseContainer as any).instance = mockContainer
      await KnowledgeBaseContainer.dispose()

      expect(mockKvStore.disconnect.called).to.be.false
    })

    it('should handle errors during disposal gracefully', async () => {
      const mockRecords = { stop: sinon.stub().rejects(new Error('Stop failed')) }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'RecordsEventProducer'),
        get: sinon.stub().returns(mockRecords),
      }

      ;(KnowledgeBaseContainer as any).instance = mockContainer
      await KnowledgeBaseContainer.dispose()
      // Should not throw, error handled gracefully
    })

    it('should do nothing when instance is null', async () => {
      ;(KnowledgeBaseContainer as any).instance = null
      await KnowledgeBaseContainer.dispose()
      // Should not throw
    })
  })
})
