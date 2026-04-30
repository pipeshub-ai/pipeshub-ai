import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { KnowledgeBaseContainer } from '../../../../src/modules/knowledge_base/container/kb_container'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import { RecordsEventProducer } from '../../../../src/modules/knowledge_base/services/records_events.service'
import { SyncEventProducer } from '../../../../src/modules/knowledge_base/services/sync_events.service'
import * as messageBrokerFactory from '../../../../src/libs/services/message-broker.factory'
import {
  BaseKafkaConnection,
  BaseKafkaProducerConnection,
} from '../../../../src/libs/services/kafka.service'

describe('KnowledgeBaseContainer - coverage', () => {
  let originalInstance: any

  beforeEach(() => {
    originalInstance = (KnowledgeBaseContainer as any).instance
    sinon.stub(messageBrokerFactory, 'resolveMessageBrokerConfig').returns({
      type: 'kafka', kafka: { brokers: ['localhost:9092'], clientId: 'test' },
    } as any)
    sinon.stub(messageBrokerFactory, 'createMessageProducer').returns({
      connect: sinon.stub().resolves(), disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(true), publish: sinon.stub().resolves(),
      publishBatch: sinon.stub().resolves(), healthCheck: sinon.stub().resolves(true),
    } as any)
    // stub so that the actual instance is not called.
    sinon.stub(BaseKafkaProducerConnection.prototype, 'connect').resolves()
    sinon.stub(BaseKafkaProducerConnection.prototype, 'disconnect').resolves()
    sinon.stub(BaseKafkaConnection.prototype, 'isConnected').returns(true)
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
      // Stub RecordsEventProducer.start to simulate failure
      sinon.stub(RecordsEventProducer.prototype, 'start').rejects(new Error('RecordsEventProducer start failed'))

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
        expect(error.message).to.include('RecordsEventProducer start failed')
      }
    })

    it('should log unknown error when non-Error is thrown during initialize', async () => {
      sinon.stub(KeyValueStoreService, 'getInstance').throws({ reason: 'non-error object' } as any)
      const logger = (KnowledgeBaseContainer as any).logger
      // Logger.getInstance() is a singleton shared across the process; another
      // test file in the same mocha worker (e.g. app.test.ts) may stub
      // Logger.prototype.error. Unwrap any pre-existing stub before re-stubbing.
      if (typeof (logger.error as any).restore === 'function') {
        (logger.error as any).restore()
      }
      const loggerErrorStub = sinon.stub(logger, 'error')

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
      } catch {
        expect(loggerErrorStub.called).to.be.true
        expect(loggerErrorStub.lastCall.args[1]).to.deep.equal({
          error: 'Unknown error',
        })
      }
    })
  })

  describe('dispose - additional coverage', () => {
    it('should disconnect MessageProducer and KeyValueStoreService', async () => {
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }
      const mockKvStore = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().resolves() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) =>
          ['MessageProducer', 'KeyValueStoreService'].includes(key),
        ),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'MessageProducer') return mockMessageProducer
          if (key === 'KeyValueStoreService') return mockKvStore
          return null
        }),
      }

      ;(KnowledgeBaseContainer as any).instance = mockContainer
      await KnowledgeBaseContainer.dispose()

      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
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
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().rejects(new Error('Disconnect failed')) }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'MessageProducer'),
        get: sinon.stub().returns(mockMessageProducer),
      }

      ;(KnowledgeBaseContainer as any).instance = mockContainer
      await KnowledgeBaseContainer.dispose()
      // Should not throw, error handled gracefully
    })

    it('should log unknown error when non-Error is thrown during disposal', async () => {
      const logger = (KnowledgeBaseContainer as any).logger
      if (typeof (logger.error as any).restore === 'function') {
        (logger.error as any).restore()
      }
      const loggerErrorStub = sinon.stub(logger, 'error')

      const mockContainer = {
        isBound: sinon.stub().throws({ reason: 'non-error object' } as any),
        get: sinon.stub(),
      }

      ;(KnowledgeBaseContainer as any).instance = mockContainer
      await KnowledgeBaseContainer.dispose()

      expect(loggerErrorStub.called).to.be.true
      expect(loggerErrorStub.lastCall.args[1]).to.deep.equal({
        error: 'Unknown error',
      })
    })

    it('should do nothing when instance is null', async () => {
      ;(KnowledgeBaseContainer as any).instance = null
      await KnowledgeBaseContainer.dispose()
      // Should not throw
    })
  })
})
