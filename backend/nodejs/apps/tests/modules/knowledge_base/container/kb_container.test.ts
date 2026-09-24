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

describe('KnowledgeBaseContainer', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should be importable', () => {
    expect(KnowledgeBaseContainer).to.be.a('function')
  })

  describe('static methods', () => {
    it('should have initialize static method', () => {
      expect(KnowledgeBaseContainer.initialize).to.be.a('function')
    })

    it('should have getInstance static method', () => {
      expect(KnowledgeBaseContainer.getInstance).to.be.a('function')
    })

    it('should have dispose static method', () => {
      expect(KnowledgeBaseContainer.dispose).to.be.a('function')
    })
  })

  describe('getInstance', () => {
    it('should throw when container is not initialized', () => {
      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = null

      try {
        expect(() => KnowledgeBaseContainer.getInstance()).to.throw(
          'Service container not initialized',
        )
      } finally {
        ;(KnowledgeBaseContainer as any).instance = originalInstance
      }
    })

    it('should return the container when initialized', () => {
      const mockContainer = { isBound: sinon.stub() }
      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = mockContainer

      try {
        const result = KnowledgeBaseContainer.getInstance()
        expect(result).to.equal(mockContainer)
      } finally {
        ;(KnowledgeBaseContainer as any).instance = originalInstance
      }
    })
  })

  describe('initialize', () => {
    it('should accept configurationManagerConfig and appConfig parameters', () => {
      expect(KnowledgeBaseContainer.initialize.length).to.equal(2)
    })
  })

  describe('dispose', () => {
    it('should not throw when instance is null', async () => {
      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = null

      try {
        await KnowledgeBaseContainer.dispose()
      } finally {
        ;(KnowledgeBaseContainer as any).instance = originalInstance
      }
    })

    it('should disconnect MessageProducer and KV store', async () => {
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

      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = mockContainer

      try {
        await KnowledgeBaseContainer.dispose()
        expect((KnowledgeBaseContainer as any).instance).to.be.null
        expect(mockMessageProducer.disconnect.calledOnce).to.be.true
        expect(mockKvStore.disconnect.calledOnce).to.be.true
      } finally {
        if ((KnowledgeBaseContainer as any).instance !== null) {
          ;(KnowledgeBaseContainer as any).instance = originalInstance
        }
      }
    })

    it('should not disconnect KV store when not connected', async () => {
      const mockKvStore = { isConnected: sinon.stub().returns(false), disconnect: sinon.stub() }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'KeyValueStoreService'),
        get: sinon.stub().callsFake((key: string) => {
          if (key === 'KeyValueStoreService') return mockKvStore
          return null
        }),
      }

      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = mockContainer

      try {
        await KnowledgeBaseContainer.dispose()
        expect(mockKvStore.disconnect.called).to.be.false
      } finally {
        if ((KnowledgeBaseContainer as any).instance !== null) {
          ;(KnowledgeBaseContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle missing service bindings gracefully', async () => {
      const mockContainer = {
        isBound: sinon.stub().returns(false),
        get: sinon.stub(),
      }

      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = mockContainer

      try {
        await KnowledgeBaseContainer.dispose()
        expect((KnowledgeBaseContainer as any).instance).to.be.null
      } finally {
        if ((KnowledgeBaseContainer as any).instance !== null) {
          ;(KnowledgeBaseContainer as any).instance = originalInstance
        }
      }
    })

    it('should handle errors during dispose gracefully', async () => {
      const mockMessageProducer = { isConnected: sinon.stub().returns(true), disconnect: sinon.stub().rejects(new Error('Disconnect failed')) }

      const mockContainer = {
        isBound: sinon.stub().callsFake((key: string) => key === 'MessageProducer'),
        get: sinon.stub().returns(mockMessageProducer),
      }

      const originalInstance = (KnowledgeBaseContainer as any).instance
      ;(KnowledgeBaseContainer as any).instance = mockContainer

      try {
        await KnowledgeBaseContainer.dispose()
        // Should not throw
      } finally {
        if ((KnowledgeBaseContainer as any).instance !== null) {
          ;(KnowledgeBaseContainer as any).instance = originalInstance
        }
      }
    })
  })
})

describe('KnowledgeBaseContainer - coverage', () => {
  let originalInstance: any
  // Dedicated sandbox (rather than the default `sinon.stub`/`sinon.restore` global
  // singleton) so this file's message-broker/Kafka stubs can't be wiped out by — or
  // wipe out — unrelated stubs from other container test files sharing the same
  // mocha `--parallel` worker process (see the Logger.error workaround below for
  // another instance of this class of cross-file pollution).
  const sandbox = sinon.createSandbox()

  beforeEach(() => {
    originalInstance = (KnowledgeBaseContainer as any).instance
    sandbox.stub(messageBrokerFactory, 'resolveMessageBrokerConfig').returns({
      type: 'kafka', kafka: { brokers: ['localhost:9092'], clientId: 'test' },
    } as any)
    sandbox.stub(messageBrokerFactory, 'createMessageProducer').returns({
      connect: sandbox.stub().resolves(), disconnect: sandbox.stub().resolves(),
      isConnected: sandbox.stub().returns(true), publish: sandbox.stub().resolves(),
      publishBatch: sandbox.stub().resolves(), healthCheck: sandbox.stub().resolves(true),
    } as any)
    // stub so that the actual instance is not called.
    sandbox.stub(BaseKafkaProducerConnection.prototype, 'connect').resolves()
    sandbox.stub(BaseKafkaProducerConnection.prototype, 'disconnect').resolves()
    sandbox.stub(BaseKafkaConnection.prototype, 'isConnected').returns(true)
  })

  afterEach(() => {
    (KnowledgeBaseContainer as any).instance = originalInstance
    sandbox.restore()
  })

  describe('initialize', () => {
    it('should create container with all bindings', async () => {
      const mockKvStore = {
        connect: sandbox.stub().resolves(),
        disconnect: sandbox.stub().resolves(),
        isConnected: sandbox.stub().returns(true),
      }
      sandbox.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)
      sandbox.stub(RecordsEventProducer.prototype, 'start').resolves()
      sandbox.stub(SyncEventProducer.prototype, 'start').resolves()

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
        // Populated even though the broker type resolves to kafka above — if the
        // `resolveMessageBrokerConfig` stub is ever bypassed, the real
        // implementation falls through to the redis branch and dereferences
        // `appConfig.redis.host`, so this must never be left undefined.
        redis: { host: 'localhost', port: 6379 },
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
        connect: sandbox.stub().rejects(new Error('KV store unreachable')),
      }
      sandbox.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)

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
        redis: { host: 'localhost', port: 6379 },
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
        connect: sandbox.stub().resolves(),
        disconnect: sandbox.stub().resolves(),
        isConnected: sandbox.stub().returns(true),
      }
      sandbox.stub(KeyValueStoreService, 'getInstance').returns(mockKvStore as any)
      // Stub RecordsEventProducer.start to simulate failure
      sandbox.stub(RecordsEventProducer.prototype, 'start').rejects(new Error('RecordsEventProducer start failed'))

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
        redis: { host: 'localhost', port: 6379 },
      } as any

      try {
        await KnowledgeBaseContainer.initialize(cmConfig as any, appConfig)
        expect.fail('Should have thrown')
      } catch (error: any) {
        expect(error.message).to.include('RecordsEventProducer start failed')
      }
    })

    it('should log unknown error when non-Error is thrown during initialize', async () => {
      sandbox.stub(KeyValueStoreService, 'getInstance').throws({ reason: 'non-error object' } as any)
      // Logger.getInstance() is a process-wide singleton; another test file in
      // the same mocha worker (e.g. app.test.ts) can leave Logger.prototype.error
      // stubbed/spied, which makes sinon.stub() on this instance fail with
      // "already stubbed". Bypass sinon by swapping the method directly.
      const logger = (KnowledgeBaseContainer as any).logger
      const errorCalls: any[][] = []
      const ownDescriptor = Object.getOwnPropertyDescriptor(logger, 'error')
      logger.error = (...args: any[]) => { errorCalls.push(args) }

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
        redis: { host: 'localhost', port: 6379 },
      } as any

      try {
        await KnowledgeBaseContainer.initialize(cmConfig as any, appConfig)
        expect.fail('Should have thrown')
      } catch {
        expect(errorCalls.length).to.be.greaterThan(0)
        expect(errorCalls[errorCalls.length - 1][1]).to.deep.equal({
          error: 'Unknown error',
        })
      } finally {
        if (ownDescriptor) {
          Object.defineProperty(logger, 'error', ownDescriptor)
        } else {
          delete logger.error
        }
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
      // See comment in initialize test above — bypass sinon for the singleton logger.
      const logger = (KnowledgeBaseContainer as any).logger
      const errorCalls: any[][] = []
      const ownDescriptor = Object.getOwnPropertyDescriptor(logger, 'error')
      logger.error = (...args: any[]) => { errorCalls.push(args) }

      const mockContainer = {
        isBound: sinon.stub().throws({ reason: 'non-error object' } as any),
        get: sinon.stub(),
      }

      ;(KnowledgeBaseContainer as any).instance = mockContainer
      try {
        await KnowledgeBaseContainer.dispose()

        expect(errorCalls.length).to.be.greaterThan(0)
        expect(errorCalls[errorCalls.length - 1][1]).to.deep.equal({
          error: 'Unknown error',
        })
      } finally {
        if (ownDescriptor) {
          Object.defineProperty(logger, 'error', ownDescriptor)
        } else {
          delete logger.error
        }
      }
    })

    it('should do nothing when instance is null', async () => {
      ;(KnowledgeBaseContainer as any).instance = null
      await KnowledgeBaseContainer.dispose()
      // Should not throw
    })
  })
})
